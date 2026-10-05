#!/usr/bin/env python3
"""Build ``index/raster.parquet``: one row per (year, tile) of the probability COGs.

Columns: year, tile_key, epsg, href (HTTPS), s3_href, size_bytes, field_frac, boundary_frac,
cropland_frac (io-lulc 2017/2020/2024 maximum, the keep-list value), the bbox columns
xmin/ymin/xmax/ymax and geometry (the tile footprint, EPSG:4326). The two fractions are read
from each COG's coarsest overview (160 m for the released tiles), so the pass costs a few
tenths of a second per tile.

The fractions are the share of overview pixels with a stored value **above** 128 (field) and
**above** 64 (boundary) -- on the COGs' 1/255 scale, p_field > 0.502 and p_boundary > 0.251.
The published 2017-2025 fractions were computed with exactly these comparisons, so widening
them to >= (p > 0.5 / p > 0.25) would silently change every published value.

    python3 tools/build_raster_index.py --cog-root /path/to/scores --years 2024 2025 \\
        --footprints tile_footprints.parquet --cropland global_cropland.parquet
    # {cog-root}/{year}/{tile}/{tile}.tif -> staging-data/index/.parts/raster_{year}.parquet

``--layout items`` (default) reads ``{cog-root}/{year}/{tile_key}/{tile_key}.tif``, which is what
``pipeline/inference/run.py`` writes. ``--layout flat`` reads ``{cog-root}/{year}/{tile_key}.tif``
and ``--layout hive`` the published bucket layout
``{cog-root}/{year}/zone=ZZ/gzd=ZZL/{tile_key}/{tile_key}.tif`` (what
``tools/move_raster_to_hierarchy.py`` produced). The hrefs written are always the published hive
paths. ``--footprints`` needs ``tile_key``, ``geometry`` (WKB), ``west``, ``south``, ``east``,
``north``; ``--cropland`` needs ``tile_key``, ``fraction``, and a tile missing from either table
is an error. A year is written on its own, into a dot-directory no uploader walks, so a year can
be indexed once its COGs are staged and folded in with ``tools/merge_raster_index.py``.
Only writes local files; upload with ``tools/upload_data.py``.
"""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import rasterio
from pyproj import CRS

ROOT = Path(__file__).resolve().parent.parent
BUCKET, PREFIX = "ftw", "global-data-2e"
HTTPS = f"https://data.source.coop/{BUCKET}/{PREFIX}"
# The published index declares GeoParquet 2.0.0 with a column-level bbox. A `covering` is not
# written: it would need a real bbox struct column, and the bbox here is four float64 columns.
GEO_VERSION = "2.0.0"
SCHEMA = pa.schema(
    [
        ("year", pa.int64()),
        ("tile_key", pa.string()),
        ("epsg", pa.int64()),
        ("href", pa.string()),
        ("s3_href", pa.string()),
        ("size_bytes", pa.int64()),
        ("field_frac", pa.float64()),
        ("boundary_frac", pa.float64()),
        ("cropland_frac", pa.float64()),
        ("xmin", pa.float64()),
        ("ymin", pa.float64()),
        ("xmax", pa.float64()),
        ("ymax", pa.float64()),
        ("geometry", pa.binary()),
    ]
)


def geo_metadata(bbox: list[float]) -> bytes:
    "The ``geo`` schema metadata for a table whose rows span ``bbox`` (xmin, ymin, xmax, ymax)."
    return json.dumps(
        {
            "version": GEO_VERSION,
            "primary_column": "geometry",
            "columns": {
                "geometry": {
                    "encoding": "WKB",
                    "geometry_types": ["Polygon"],
                    "bbox": bbox,
                    "crs": json.loads(CRS("EPSG:4326").to_json()),
                }
            },
        }
    ).encode()


def hive_key(year: int, tile_key: str) -> str:
    "Key under ``raster/``: ``{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif``."
    return f"{year}/zone={tile_key[:2]}/gzd={tile_key[:3]}/{tile_key}/{tile_key}.tif"


GLOBS = {"items": "*/*.tif", "flat": "*.tif", "hive": "zone=*/gzd=*/*/*.tif"}


def cog_paths(cog_root: Path, year: int, layout: str) -> list[Path]:
    "The tile COGs of one year under ``cog_root`` in the given on-disk layout."
    return sorted((cog_root / str(year)).glob(GLOBS[layout]))


def stats(path: Path) -> tuple[str, int, float, float, int]:
    "(tile_key, epsg, field_frac, boundary_frac, size_bytes) from the coarsest overview."
    with rasterio.open(path) as ds:
        epsg = ds.crs.to_epsg() if ds.crs else None
        if epsg is None:
            raise SystemExit(f"{path}: CRS is missing or has no EPSG code; refusing to index epsg 0")
        overviews = ds.overviews(1)
        factor = max(overviews) if overviews else 64
        shape = (max(1, ds.height // factor), max(1, ds.width // factor))
        a = ds.read(out_shape=(2, *shape))
        return (
            path.stem,
            epsg,
            # > 128 and > 64, not >=: see the module docstring, the published values depend on it
            float((a[0] > 128).mean()),
            float((a[1] > 64).mean()),
            path.stat().st_size,
        )


def footprints(footprints_path: Path, cropland_path: Path) -> dict[str, tuple]:
    rows = duckdb.sql(
        f"select f.tile_key, f.geometry, f.west, f.south, f.east, f.north, c.fraction "
        f"from '{footprints_path}' f left join '{cropland_path}' c using (tile_key)"
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def year_rows(year: int, paths: list[Path], meta: dict[str, tuple], workers: int) -> list[dict]:
    rows = []
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            results = list(pool.map(stats, paths, chunksize=8))
    else:
        results = [stats(p) for p in paths]
    for tile_key, epsg, field_frac, boundary_frac, size in results:
        if tile_key not in meta:
            raise SystemExit(f"{year}/{tile_key}: no footprint in the footprints table")
        wkb, w, s, e, n, crop = meta[tile_key]
        if crop is None:
            # the left join found no cropland row: fail closed rather than publish a null
            raise SystemExit(f"{year}/{tile_key}: no cropland fraction in the cropland table")
        rows.append(
            {
                "year": year,
                "tile_key": tile_key,
                "epsg": epsg,
                "href": f"{HTTPS}/raster/{hive_key(year, tile_key)}",
                "s3_href": f"s3://{BUCKET}/{PREFIX}/raster/{hive_key(year, tile_key)}",
                "size_bytes": size,
                "field_frac": field_frac,
                "boundary_frac": boundary_frac,
                "cropland_frac": crop,
                "xmin": w,
                "ymin": s,
                "xmax": e,
                "ymax": n,
                "geometry": bytes(wkb),
            }
        )
    return rows


def table_bbox(table: pa.Table) -> list[float]:
    "The (xmin, ymin, xmax, ymax) extent of a table's bbox columns."
    return [
        pc.min(table["xmin"]).as_py(),
        pc.min(table["ymin"]).as_py(),
        pc.max(table["xmax"]).as_py(),
        pc.max(table["ymax"]).as_py(),
    ]


def write_index(rows: list[dict], out: Path) -> pa.Table:
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    table = table.replace_schema_metadata({b"geo": geo_metadata(table_bbox(table))})
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    return table


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--cog-root", type=Path, required=True)
    ap.add_argument("--footprints", type=Path, required=True)
    ap.add_argument("--cropland", type=Path, required=True)
    ap.add_argument("--layout", choices=tuple(GLOBS), default="items")
    # a dot-directory: tools/publish.py and tools/upload_data.py both skip it, so the per-year
    # parts cannot reach the bucket. Only the merged raster.parquet belongs in staging-data/index.
    ap.add_argument("--out-dir", type=Path, default=ROOT / "staging-data" / "index" / ".parts")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    meta = footprints(a.footprints, a.cropland)
    for year in a.years:
        paths = cog_paths(a.cog_root, year, a.layout)
        if not paths:
            raise SystemExit(
                f"no COGs matching {GLOBS[a.layout]!r} under {a.cog_root / str(year)}; "
                "is --layout right?"
            )
        rows = year_rows(year, paths, meta, a.workers)
        out = a.out_dir / f"raster_{year}.parquet"
        table = write_index(rows, out)
        print(f"{year}: {table.num_rows:,} tiles -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
