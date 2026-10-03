#!/usr/bin/env python3
"""Build ``index/vector.parquet``: one row per (year, UTM zone) fiboa file.

Columns: year, zone, href (HTTPS), s3_href, size_bytes, n_parcels, area_km2, the bbox
columns xmin/ymin/xmax/ymax and geometry (the bbox of the file's parcels, EPSG:4326). Stats come
from each file's ``bbox`` struct and ``metrics:area`` columns, so the pass reads no geometry.
Fails if a year does not have exactly ``--expected-zones`` files, or a file's scanned row count
differs from its footer.

    python3 tools/build_vector_index.py --fiboa-root /path/to/hive --years 2024 2025
    # {fiboa-root}/{year}/zone=NN/utmNN.parquet -> staging-data/index/vector.parquet

Only writes a local file; upload it with ``tools/upload_data.py``.
"""
import argparse
import re
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from pyproj import CRS

ROOT = Path(__file__).resolve().parent.parent
BUCKET, PREFIX = "ftw", "global-data-2e"
HTTPS = f"https://data.source.coop/{BUCKET}/{PREFIX}"
EXPECTED_ZONES = 54
GEO = (
    b'{"version":"1.1.0","primary_column":"geometry","columns":{"geometry":'
    b'{"encoding":"WKB","geometry_types":["Polygon"],'
    b'"crs":' + CRS("EPSG:4326").to_json().encode() + b',"covering":{"bbox":'
    b'{"xmin":["xmin"],"ymin":["ymin"],"xmax":["xmax"],"ymax":["ymax"]}}}}}'
)


def zone_row(year: int, f: Path) -> dict:
    m = re.fullmatch(r"utm(\d\d)\.parquet", f.name)
    if m is None:
        raise SystemExit(f"unexpected zone file name: {f.name}")
    zone = int(m.group(1))
    row = duckdb.sql(
        f'select count(*), sum("metrics:area"), min(bbox.xmin), min(bbox.ymin), '
        f"max(bbox.xmax), max(bbox.ymax) from '{f}'"
    ).fetchone()
    n, area, w, s, e, north = row
    footer = pq.ParquetFile(f).metadata.num_rows
    if footer != n:
        raise SystemExit(f"{f}: footer says {footer:,} rows, scan found {n:,}")
    # zone files are EPSG:4326 (fiboa default); the UTM zone is the parcels' native grid
    return {
        "year": year,
        "zone": zone,
        "href": f"{HTTPS}/vector/{year}/zone={zone:02d}/{f.name}",
        "s3_href": f"s3://{BUCKET}/{PREFIX}/vector/{year}/zone={zone:02d}/{f.name}",
        "size_bytes": f.stat().st_size,
        "n_parcels": int(n),
        "area_km2": float(area) / 1e6,
        "xmin": w,
        "ymin": s,
        "xmax": e,
        "ymax": north,
        "geometry": shapely.to_wkb(shapely.box(w, s, e, north)),
    }


def build_rows(root: Path, years: list[int], expected_zones: int) -> list[dict]:
    rows = []
    for y in years:
        files = sorted((root / str(y)).glob("zone=*/utm*.parquet"))
        if len(files) != expected_zones:
            raise SystemExit(f"{y}: {len(files)} zone files under {root}, expected {expected_zones}")
        rows += [zone_row(y, f) for f in files]
        print(f"{y}: {len(files)} zone files", flush=True)
    return rows


def write_index(rows: list[dict], out: Path) -> pa.Table:
    table = pa.Table.from_pylist(rows).replace_schema_metadata({b"geo": GEO})
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    return table


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--fiboa-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "staging-data" / "index" / "vector.parquet")
    ap.add_argument("--expected-zones", type=int, default=EXPECTED_ZONES)
    a = ap.parse_args()
    rows = build_rows(a.fiboa_root, a.years, a.expected_zones)
    table = write_index(rows, a.out)
    print(f"{a.out}: {table.num_rows} rows, {sum(r['n_parcels'] for r in rows):,} parcels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
