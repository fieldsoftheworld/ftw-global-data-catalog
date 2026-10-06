#!/usr/bin/env python3
"""Build ``index/vector.parquet``: one row per (year, UTM zone) fiboa file.

Columns: year, zone, href (HTTPS), s3_href, size_bytes, n_parcels, area_km2, the bbox
columns xmin/ymin/xmax/ymax and geometry (the bbox of the file's parcels, EPSG:4326). Stats come
from each file's ``bbox`` struct and ``metrics:area`` columns, so the pass reads no geometry.
Fails if a year does not have exactly ``--expected-zones`` files, if a file's scanned row count
differs from its footer, or if a zone file holds no parcels.

    python3 tools/build_vector_index.py --fiboa-root /path/to/hive --years 2024 2025
    # {fiboa-root}/{year}/zone=NN/utmNN.parquet -> staging-data/index/vector.parquet

The whole index is written in one pass, so ``--years`` must cover every year already in the
file being overwritten -- indexing one new year over the published nine-year index would drop
the other eight. ``--force`` skips that check.

Only writes a local file; upload it with ``tools/upload_data.py``.
"""
import argparse
import json
import re
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely
from pyproj import CRS

ROOT = Path(__file__).resolve().parent.parent
BUCKET, PREFIX = "ftw", "global-data-2e"
HTTPS = f"https://data.source.coop/{BUCKET}/{PREFIX}"
EXPECTED_ZONES = 54
# A column-level bbox, which is what the published index carries. No `covering`: that needs a
# two-element path into a real bbox struct column, and the bbox here is four float64 columns.
GEO_VERSION = "1.1.0"
SCHEMA = pa.schema(
    [
        ("year", pa.int64()),
        ("zone", pa.int64()),
        ("href", pa.string()),
        ("s3_href", pa.string()),
        ("size_bytes", pa.int64()),
        ("n_parcels", pa.int64()),
        ("area_km2", pa.float64()),
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
    if not n or area is None:
        raise SystemExit(f"{f}: {n or 0} parcels, or no metrics:area to total")
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


def check_years_covered(out: Path, years: list[int]) -> None:
    "Refuse to drop years that ``out`` already holds and ``--years`` does not rebuild."
    if not out.exists():
        return
    missing = sorted(set(pq.read_table(out, columns=["year"])["year"].to_pylist()) - set(years))
    if missing:
        raise SystemExit(
            f"{out} already holds {missing}, which --years does not rebuild; this writes the "
            "whole index, so those rows would be dropped. Add them to --years or pass --force"
        )


def write_index(rows: list[dict], out: Path) -> pa.Table:
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    bbox = [
        pc.min(table["xmin"]).as_py(),
        pc.min(table["ymin"]).as_py(),
        pc.max(table["xmax"]).as_py(),
        pc.max(table["ymax"]).as_py(),
    ]
    table = table.replace_schema_metadata({b"geo": geo_metadata(bbox)})
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    return table


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--fiboa-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "staging-data" / "index" / "vector.parquet")
    ap.add_argument("--expected-zones", type=int, default=EXPECTED_ZONES)
    ap.add_argument("--force", action="store_true", help="overwrite --out even if it holds more years")
    a = ap.parse_args()
    if not a.force:
        check_years_covered(a.out, a.years)
    rows = build_rows(a.fiboa_root, a.years, a.expected_zones)
    table = write_index(rows, a.out)
    print(f"{a.out}: {table.num_rows} rows, {sum(r['n_parcels'] for r in rows):,} parcels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
