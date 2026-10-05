#!/usr/bin/env python3
"""The vector and raster index builders and the year merge, on tiny synthetic data.

Checks the column contract the published manifests carry: hive hrefs, counts, areas,
fractions read from the coarsest overview, valid GeoParquet metadata, and that a merge
replaces a year's rows in place rather than appending to them. No network, no AWS.

Needs pyarrow, numpy, duckdb, shapely, pyproj and rasterio; without one it skips, so a
checkout that only validates metadata still runs the rest of ``tests/run_all.py``.

Run: python3 tests/test_indexes.py
"""
import json
import sys
import tempfile
import warnings
from pathlib import Path

try:
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import rasterio
    import shapely
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
except ImportError as exc:  # pragma: no cover - depends on the local environment
    print(f"skip  test_indexes.py: {exc.name} is not installed")
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_raster_index as bri  # noqa: E402
import build_vector_index as bvi  # noqa: E402
import merge_raster_index as mri  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def geo_of(table: pa.Table) -> dict:
    "The primary geometry column's ``geo`` metadata."
    geo = json.loads(table.schema.metadata[b"geo"])
    return geo["columns"][geo["primary_column"]] | {"version": geo["version"]}


def zone_file(root: Path, year: int, zone: int, areas: list[float], x0: float) -> Path:
    path = root / str(year) / f"zone={zone:02d}" / f"utm{zone:02d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(areas)
    bbox = [
        {"xmin": x0 + i, "ymin": 40.0 + i, "xmax": x0 + i + 0.5, "ymax": 40.5 + i} for i in range(n)
    ]
    bbox_type = pa.struct([(k, pa.float64()) for k in ("xmin", "ymin", "xmax", "ymax")])
    pq.write_table(
        pa.table(
            {
                "id": pa.array([f"a-{i}" for i in range(n)], pa.string()),
                "bbox": pa.array(bbox, bbox_type),
                "metrics:area": pa.array(areas, pa.float64()),
            }
        ),
        path,
    )
    return path


def score_cog(root: Path, year: int, tile: str, field: int, boundary: int) -> Path:
    path = root / str(year) / f"{tile}.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", height=256, width=256, count=2, dtype="uint8",
        crs="EPSG:32615", transform=from_origin(500000, 4600000, 2.5, 2.5),
        tiled=True, blockxsize=128, blockysize=128,
    ) as ds:
        ds.write(np.stack([np.full((256, 256), field, np.uint8),
                           np.full((256, 256), boundary, np.uint8)]))
        ds.build_overviews([4, 8], Resampling.average)
    return path


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # --- vector index -------------------------------------------------
    hive = tmp / "fiboa"
    zone_file(hive, 2025, 15, [2e6, 3e6], -95.0)
    zone_file(hive, 2025, 16, [1e6], -89.0)
    rows = bvi.build_rows(hive, [2025], 2)
    check([r["zone"] for r in rows] == [15, 16], "zones sorted")
    check(rows[0]["n_parcels"] == 2 and abs(rows[0]["area_km2"] - 5.0) < 1e-9, "count and area_km2")
    check(rows[0]["href"] == "https://data.source.coop/ftw/global-data-2e/vector/2025/zone=15/utm15.parquet",
          "hive https href")
    check(rows[0]["s3_href"] == "s3://ftw/global-data-2e/vector/2025/zone=15/utm15.parquet", "s3 href")
    check((rows[0]["xmin"], rows[0]["ymax"]) == (-95.0, 41.5), "bbox from the bbox struct")
    vout = tmp / "index" / "vector.parquet"
    vtable = bvi.write_index(rows, vout)
    check(vtable.num_rows == 2, "two zone rows")
    check(vtable.schema.types == bvi.SCHEMA.types, "the vector schema is the declared one")

    # the geo metadata is valid GeoParquet: a column-level bbox over the rows, no covering
    vgeo = geo_of(vtable)
    check(vgeo["version"] == "1.1.0", f"vector geo version {vgeo['version']}")
    check("covering" not in vgeo, "no covering (its paths would have to name a struct column)")
    check(vgeo["bbox"] == [-95.0, 40.0, -88.5, 41.5], f"vector bbox {vgeo.get('bbox')}")
    check(b"geo" in pq.read_schema(vout).metadata, "geo metadata survives the write")

    try:
        bvi.build_rows(hive, [2025], 54)
        check(False, "a wrong zone count must fail")
    except SystemExit as exc:
        check("expected 54" in str(exc), "zone-count message")

    # an empty zone file must report itself, not raise TypeError on a null sum
    empty = zone_file(tmp / "empty", 2025, 15, [], -95.0)
    try:
        bvi.zone_row(2025, empty)
        check(False, "a zone file with no parcels must fail")
    except SystemExit as exc:
        check("0 parcels" in str(exc), f"empty-zone message: {exc}")

    # writing the whole index for fewer years than it holds would drop the rest
    try:
        bvi.check_years_covered(vout, [2024])
        check(False, "dropping a year already in the index must fail")
    except SystemExit as exc:
        check("[2025]" in str(exc), f"year-coverage message: {exc}")
    bvi.check_years_covered(vout, [2024, 2025])  # covers it: no error
    bvi.check_years_covered(tmp / "index" / "absent.parquet", [2025])  # nothing to drop

    # --- raster index -------------------------------------------------
    cogs = tmp / "scores"
    score_cog(cogs, 2025, "15TVG_0_0", field=200, boundary=100)  # both above the thresholds
    score_cog(cogs, 2025, "15TWG_0_0", field=100, boundary=50)  # both below them
    fp = tmp / "fp.parquet"
    pq.write_table(
        pa.table({
            "tile_key": ["15TVG_0_0", "15TWG_0_0", "15TXG_0_0"],
            "geometry": [shapely.to_wkb(shapely.box(-94, 41, -93, 42)),
                         shapely.to_wkb(shapely.box(-93, 41, -92, 42)),
                         shapely.to_wkb(shapely.box(-92, 41, -91, 42))],
            "west": [-94.0, -93.0, -92.0], "south": [41.0, 41.0, 41.0],
            "east": [-93.0, -92.0, -91.0], "north": [42.0, 42.0, 42.0],
        }), fp)
    crop = tmp / "crop.parquet"
    pq.write_table(pa.table({"tile_key": ["15TVG_0_0", "15TWG_0_0"], "fraction": [0.4, 0.6]}), crop)
    meta = bri.footprints(fp, crop)
    rrows = bri.year_rows(2025, sorted((cogs / "2025").glob("*.tif")), meta, workers=1)
    a, b = rrows
    check(a["tile_key"] == "15TVG_0_0" and a["epsg"] == 32615, "tile key and epsg from the COG")
    check(a["field_frac"] == 1.0 and a["boundary_frac"] == 1.0, "fractions above the thresholds")
    check(b["field_frac"] == 0.0 and b["boundary_frac"] == 0.0, "fractions below the thresholds")
    check(a["href"].endswith("/raster/2025/zone=15/gzd=15T/15TVG_0_0/15TVG_0_0.tif"), "hive href")
    check(a["cropland_frac"] == 0.4 and (a["xmin"], a["ymax"]) == (-94.0, 42.0),
          "cropland and footprint bbox joined by tile_key")
    try:
        bri.year_rows(2025, sorted((cogs / "2025").glob("*.tif")), {}, workers=1)
        check(False, "a tile without a footprint must fail")
    except SystemExit as exc:
        check("no footprint" in str(exc), "footprint message")

    # the thresholds are strict: a tile sitting exactly on 128/64 counts as below.
    # The published 2017-2025 fractions were computed this way; see the builder docstring.
    edge = tmp / "edge"
    edge_path = score_cog(edge, 2025, "15TXG_0_0", field=128, boundary=64)
    _, _, edge_field, edge_boundary, _ = bri.stats(edge_path)
    check(edge_field == 0.0, f"a pixel value of exactly 128 is not field ({edge_field})")
    check(edge_boundary == 0.0, f"a pixel value of exactly 64 is not boundary ({edge_boundary})")

    # a tile with no cropland row must fail closed, not write a null into an advertised column
    nocrop = tmp / "nocrop.parquet"
    pq.write_table(pa.table({"tile_key": ["99ZZZ_0_0"], "fraction": [0.1]}), nocrop)
    try:
        bri.year_rows(2025, [edge_path], bri.footprints(fp, nocrop), workers=1)
        check(False, "a tile without a cropland fraction must fail")
    except SystemExit as exc:
        check("no cropland fraction" in str(exc), f"cropland message: {exc}")

    # --- layouts: what the pipeline writes, flat, and the published hive layout ---
    items_root = tmp / "items"
    score_cog(items_root / "x", 2025, "15TVG_0_0", field=200, boundary=100)
    item_dir = items_root / "2025" / "15TVG_0_0"
    item_dir.mkdir(parents=True)
    (items_root / "x" / "2025" / "15TVG_0_0.tif").rename(item_dir / "15TVG_0_0.tif")
    check([p.name for p in bri.cog_paths(items_root, 2025, "items")] == ["15TVG_0_0.tif"],
          "pipeline/inference/run.py's raster/{year}/{tile}/{tile}.tif is globbed")
    check(bri.cog_paths(items_root, 2025, "flat") == [], "an item tree is not read as flat")

    hive_root = tmp / "hive-cogs"
    score_cog(hive_root / "x", 2025, "15TVG_0_0", field=200, boundary=100)
    nested = hive_root / "2025" / "zone=15" / "gzd=15T" / "15TVG_0_0"
    nested.mkdir(parents=True)
    (hive_root / "x" / "2025" / "15TVG_0_0.tif").rename(nested / "15TVG_0_0.tif")
    check([p.name for p in bri.cog_paths(hive_root, 2025, "hive")] == ["15TVG_0_0.tif"],
          "hive layout globbed")
    check(bri.cog_paths(hive_root, 2025, "flat") == [], "a hive tree is not read as flat")
    check(len(bri.cog_paths(cogs, 2025, "flat")) == 2 and bri.cog_paths(cogs, 2025, "hive") == [],
          "a flat tree is not read as hive")

    # --- an unknown CRS must raise, not be written as epsg 0 -----------
    warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
    nocrs = tmp / "nocrs.tif"
    with rasterio.open(nocrs, "w", driver="GTiff", height=8, width=8, count=2, dtype="uint8") as ds:
        ds.write(np.zeros((2, 8, 8), np.uint8))
    try:
        bri.stats(nocrs)
        check(False, "a COG without an EPSG code must fail")
    except SystemExit as exc:
        check("epsg 0" in str(exc), "unknown-CRS message")

    # --- the raster geo metadata matches the published index ------------
    main = tmp / "index" / "raster.parquet"
    part = tmp / "index" / ".parts" / "raster_2025.parquet"
    rtable = bri.write_index(rrows, part)
    check(rtable.schema.types == bri.SCHEMA.types, "the raster schema is the declared one")
    rgeo = geo_of(rtable)
    check(rgeo["version"] == "2.0.0", f"raster geo version {rgeo['version']}")
    check(rgeo["geometry_types"] == ["Polygon"], f"geometry types {rgeo['geometry_types']}")
    check("covering" not in rgeo, "no covering in the raster geo either")
    check(rgeo["bbox"] == [-94.0, 41.0, -92.0, 42.0], f"raster bbox {rgeo.get('bbox')}")

    # --- merge: the year's rows are replaced, not appended to ----------
    # main holds 2024 and a stale 2025 whose field_frac is nothing the builder would produce
    stale = [{**r, "field_frac": 0.123} for r in rrows]
    bri.write_index([{**r, "year": 2024, "field_frac": 0.5} for r in rrows] + stale, main)
    merged, replaced = mri.merge(main, part, 2025)
    check(merged["year"].to_pylist() == [2024, 2024, 2025, 2025], "merged years sorted")
    check(replaced == 2, f"two stale 2025 rows replaced ({replaced})")
    fresh = merged.filter([y == 2025 for y in merged["year"].to_pylist()])["field_frac"].to_pylist()
    check(fresh == [1.0, 0.0], f"the stale 2025 field_frac is gone ({fresh})")
    check(0.123 not in merged["field_frac"].to_pylist(), "no stale row survives the merge")
    check(merged["field_frac"].to_pylist() == [0.5, 0.5, 1.0, 0.0],
          f"the 2024 rows are untouched ({merged['field_frac'].to_pylist()})")
    check(geo_of(merged)["bbox"] == [-94.0, 41.0, -92.0, 42.0],
          f"the merged bbox is recomputed ({geo_of(merged)['bbox']})")

    # a part with fewer rows than the year it replaces is refused unless asked for
    short = tmp / "index" / ".parts" / "short_2025.parquet"
    bri.write_index(rrows[:1], short)
    try:
        mri.merge(main, short, 2025)
        check(False, "a part that shrinks the year must fail")
    except SystemExit as exc:
        check("--allow-shrink" in str(exc), f"shrink message: {exc}")
    shrunk, replaced = mri.merge(main, short, 2025, allow_shrink=True)
    check(shrunk.num_rows == 3 and replaced == 2, "--allow-shrink accepts the smaller year")

    # a main index that does not exist yet is an empty base, so the first merge bootstraps it
    boot, replaced = mri.merge(tmp / "index" / "absent.parquet", part, 2025)
    check(boot.num_rows == 2 and replaced == 0, "a missing main index merges as empty")
    check(geo_of(boot)["bbox"] == [-94.0, 41.0, -92.0, 42.0], "the bootstrap keeps the bbox")

    try:
        mri.merge(main, main, 2025)
        check(False, "a part holding another year must fail")
    except SystemExit as exc:
        check("other than 2025" in str(exc), "merge year check")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: index builders hold their column contract")
