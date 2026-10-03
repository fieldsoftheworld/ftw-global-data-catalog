#!/usr/bin/env python3
"""Stage the 2e FTW field predictions for one year into merged GeoParquet.

Reads ``vector/{year}/utm{NN}.parquet`` (54 zone files) from the source.coop
data proxy and spools each to a local parquet, carrying **all columns except
the redundant ``bbox`` struct** (GeoParquet 2.0 native stats replace it; the
``collection`` column stays in the parquet and is excluded from tiles at
tiling time). The sbatch wrapper then merges with ``gpio extract`` and
converts to GeoParquet 2.0.

Measured before writing this (2026-09-28, both checks in the session ledger;
measured on the first bucket revision, whose parcel set — counts, geometries,
areas — is identical to the current 9-column revision):

- **No dedupe.** Zones partition parcels cleanly: in the 6°E strip between
  utm31 and utm32 (2025), 0 of 23,290/4,441 parcels share a geometry or id
  across the files.
- **No area cutoff.** max(metrics:area) = 5.007 km²; upstream already
  removed parcels > 5 km² (17 sit a rounding hair above).
- **Timezone.** determination:datetime is a TIMESTAMP WITH TIME ZONE column
  of year-end UTC markers; the session runs with TimeZone='UTC' so nothing
  downstream can shift it.

Use https:// URLs, never s3:// — DuckDB's s3 path hangs on rails compute
nodes probing the (blackholed) EC2 metadata service.

    python3 stage_global.py --year 2025
"""
import argparse
import os
import time
from pathlib import Path

import duckdb

INDEX = "https://data.source.coop/ftw/global-data-2e/index/vector.parquet"
# SRC_ROOT=/projects/.../ftw-fiboa-v3 reads the local hive tree ({year}/zone=NN/utmNN.parquet)
# instead of the proxy: same bytes as the bucket, no network.
SRC_ROOT = os.environ.get("SRC_ROOT")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--work", type=Path, default=Path.cwd())
    args = parser.parse_args()

    staged = args.work / "staged" / str(args.year)
    staged.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-2e-pipeline)"}
    )
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET TimeZone='UTC';")  # year markers are UTC midnights
    con.execute("SET memory_limit='48GB'; SET preserve_insertion_order=false;"
                "SET threads=16;")
    con.execute("SET http_retries=8; SET http_retry_wait_ms=2000;"
                "SET http_timeout=120000;")

    if SRC_ROOT:
        hrefs = [(str(f),) for f in sorted(Path(SRC_ROOT, str(args.year)).glob("zone=*/utm*.parquet"))]
    else:
        hrefs = con.execute(
            f"SELECT href FROM '{INDEX}' WHERE year = ? ORDER BY zone",
            [args.year],
        ).fetchall()
    if not hrefs:
        raise SystemExit(f"no index rows for year {args.year}")

    t0 = time.time()
    done = skipped = 0
    rows_total = 0
    for i, (href,) in enumerate(hrefs, 1):
        name = href.rsplit("/", 1)[-1]
        out = staged / name
        if out.exists():
            skipped += 1
            continue
        tmp = out.with_suffix(".parquet.tmp")
        con.execute(f"""
            COPY (SELECT * EXCLUDE (bbox) FROM read_parquet('{href}'))
            TO '{tmp}' (FORMAT PARQUET, COMPRESSION zstd,
                        ROW_GROUP_SIZE 65536)
        """)
        tmp.rename(out)
        n = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
        rows_total += n
        done += 1
        print(f"[{i}/{len(hrefs)}] {name}: {n:,} rows, "
              f"{out.stat().st_size / 1e6:.0f} MB "
              f"({(time.time() - t0) / 60:.0f} min elapsed)", flush=True)

    print(f"staged {done} zone(s) ({skipped} already present), "
          f"{rows_total:,} new rows, {(time.time() - t0) / 60:.1f} min",
          flush=True)


if __name__ == "__main__":
    main()
