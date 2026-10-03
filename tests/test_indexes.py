#!/usr/bin/env python3
"""The vector and raster index builders and the year merge, on tiny synthetic data.

Checks the column contract the published manifests carry: hive hrefs, counts, areas,
fractions read from the coarsest overview, and that a merge replaces a year in place.
No network, no AWS.

Run: python3 tests/test_indexes.py
"""
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import shapely
from rasterio.enums import Resampling
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_raster_index as bri  # noqa: E402
import build_vector_index as bvi  # noqa: E402
import merge_raster_index as mri  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def zone_file(root: Path, year: int, zone: int, areas: list[float], x0: float) -> None:
    path = root / str(year) / f"zone={zone:02d}" / f"utm{zone:02d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(areas)
    bbox = [
        {"xmin": x0 + i, "ymin": 40.0 + i, "xmax": x0 + i + 0.5, "ymax": 40.5 + i} for i in range(n)
    ]
    pq.write_table(
        pa.table({"id": [f"a-{i}" for i in range(n)], "bbox": bbox, "metrics:area": areas}), path
    )


def score_cog(root: Path, year: int, tile: str, field: int, boundary: int) -> None:
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
    vtable = bvi.write_index(rows, tmp / "index" / "vector.parquet")
    check(b"geo" in pq.read_schema(tmp / "index" / "vector.parquet").metadata, "geo metadata")
    check(vtable.num_rows == 2, "two zone rows")
    try:
        bvi.build_rows(hive, [2025], 54)
        check(False, "a wrong zone count must fail")
    except SystemExit as exc:
        check("expected 54" in str(exc), "zone-count message")

    # --- raster index -------------------------------------------------
    cogs = tmp / "scores"
    score_cog(cogs, 2025, "15TVG_0_0", field=200, boundary=100)  # both above the thresholds
    score_cog(cogs, 2025, "15TWG_0_0", field=100, boundary=50)  # both below them
    fp = tmp / "fp.parquet"
    pq.write_table(
        pa.table({
            "tile_key": ["15TVG_0_0", "15TWG_0_0"],
            "geometry": [shapely.to_wkb(shapely.box(-94, 41, -93, 42)),
                         shapely.to_wkb(shapely.box(-93, 41, -92, 42))],
            "west": [-94.0, -93.0], "south": [41.0, 41.0], "east": [-93.0, -92.0], "north": [42.0, 42.0],
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

    # --- layouts: flat vs the published hive layout ---------------------
    hive_root = tmp / "hive"
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

    # --- merge: a year is replaced, others kept, sorted, bbox recomputed -
    main = tmp / "index" / "raster.parquet"
    bri.write_index([{**r, "year": 2024} for r in rrows], main)
    bri.write_index(rrows, tmp / "index" / "raster_2025.parquet")
    merged = mri.merge(main, tmp / "index" / "raster_2025.parquet", 2025)
    check(merged["year"].to_pylist() == [2024, 2024, 2025, 2025], "merged years sorted")
    bri.write_index([{**r, "field_frac": 0.5} for r in rrows[:1]], tmp / "index" / "raster_2025.parquet")
    again = mri.merge(main, tmp / "index" / "raster_2025.parquet", 2025)
    check(again.num_rows == 3, "a rerun replaces that year's rows")
    try:
        mri.merge(main, main, 2025)
        check(False, "a part holding another year must fail")
    except SystemExit as exc:
        check("other than 2025" in str(exc), "merge year check")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: index builders hold their column contract")
