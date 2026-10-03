#!/usr/bin/env python3
"""Build ``index/raster.parquet``: one row per (year, tile) of the probability COGs.

Columns: year, tile_key, epsg, href (HTTPS), s3_href, size_bytes, field_frac (share of pixels
with p_field > 0.5), boundary_frac (p_boundary > 0.25), cropland_frac (io-lulc 2017/2020/2024
maximum, the keep-list value), the bbox columns xmin/ymin/xmax/ymax and geometry (the tile
footprint, EPSG:4326). The two fractions are read from each COG's coarsest overview (160 m for
the released tiles), so the pass costs a few tenths of a second per tile.

    python3 tools/build_raster_index.py --cog-root /path/to/scores --years 2024 2025 \\
        --footprints tile_footprints.parquet --cropland global_cropland.parquet
    # {cog-root}/{year}/{tile_key}.tif (flat) -> staging-data/index/raster_{year}.parquet per year

The score COGs are flat files named ``{tile_key}.tif``; the hrefs written are the published hive
paths. ``--footprints`` needs ``tile_key``, ``geometry`` (WKB), ``west``, ``south``, ``east``,
``north``; ``--cropland`` needs ``tile_key``, ``fraction``. A year is written on its own so a year
can be indexed once its COGs are staged and folded in with ``tools/merge_raster_index.py``.
Only writes local files; upload with ``tools/upload_data.py``.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
from pyproj import CRS

ROOT = Path(__file__).resolve().parent.parent
BUCKET, PREFIX = "ftw", "global-data-2e"
HTTPS = f"https://data.source.coop/{BUCKET}/{PREFIX}"
GEO = (
    b'{"version":"1.1.0","primary_column":"geometry","columns":{"geometry":'
    b'{"encoding":"WKB","geometry_types":["Polygon","MultiPolygon"],'
    b'"crs":' + CRS("EPSG:4326").to_json().encode() + b',"covering":{"bbox":'
    b'{"xmin":["xmin"],"ymin":["ymin"],"xmax":["xmax"],"ymax":["ymax"]}}}}}'
)


def hive_key(year: int, tile_key: str) -> str:
    "Key under ``raster/``: ``{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif``."
    return f"{year}/zone={tile_key[:2]}/gzd={tile_key[:3]}/{tile_key}/{tile_key}.tif"


def stats(path: Path) -> tuple[str, int, float, float, int]:
    "(tile_key, epsg, field_frac, boundary_frac, size_bytes) from the coarsest overview."
    with rasterio.open(path) as ds:
        overviews = ds.overviews(1)
        factor = max(overviews) if overviews else 64
        shape = (max(1, ds.height // factor), max(1, ds.width // factor))
        a = ds.read(out_shape=(2, *shape))
        return (
            path.stem,
            ds.crs.to_epsg() or 0,
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


def write_index(rows: list[dict], out: Path) -> pa.Table:
    table = pa.Table.from_pylist(rows).replace_schema_metadata({b"geo": GEO})
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    return table


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--cog-root", type=Path, required=True)
    ap.add_argument("--footprints", type=Path, required=True)
    ap.add_argument("--cropland", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "staging-data" / "index")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    meta = footprints(a.footprints, a.cropland)
    for year in a.years:
        paths = sorted((a.cog_root / str(year)).glob("*.tif"))
        if not paths:
            raise SystemExit(f"no COGs under {a.cog_root / str(year)}")
        rows = year_rows(year, paths, meta, a.workers)
        out = a.out_dir / f"raster_{year}.parquet"
        table = write_index(rows, out)
        print(f"{year}: {table.num_rows:,} tiles -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
