#!/usr/bin/env python3
"""Stage FTW global field predictions into per-country, per-year GeoParquet.

Reads the source partitions (results-by-admin-conf) from the source.coop data
proxy and writes staged/<year>/<CC>.parquet with columns
(area, confidence, country, state, geometry).

Key behaviors:
  - Both years staged in ONE network pass per country (each source file is
    only downloaded once, spooled to a local temp parquet, then split).
  - Deduplication: fields straddling admin subdivision boundaries appear once
    per subdivision in the source with identical id/geometry/area
    (ftw-data-catalog#9). We keep one row per (id, area) within a country,
    preferring the lowest subdivision code.
  - Timezone: `determination:datetime` values are midnight-UTC year markers.
    DuckDB's date_part() uses the session timezone, which on rails
    (America/Chicago) shifts every year down by one — SET TimeZone='UTC' is
    load-bearing.
  - Resumable: countries whose outputs exist for all requested years are
    skipped, so a re-run sweeps stragglers.
  - Optional --max-area-km2 drops implausibly large "fields" (the >=100 km2
    bucket is dominated by low-confidence Sahara artifacts).

Use https:// URLs, never s3:// — DuckDB's s3 path hangs on rails compute
nodes probing the (blackholed) EC2 metadata service.
"""
import argparse
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb

BASE = "https://data.source.coop/ftw/"
PREFIX = "predictions/vectors/alpha/results-by-admin-conf/"
YEARS = (2024, 2025)


def build_manifest(cache: Path) -> list[str]:
    """List every source parquet via the data proxy's S3-style list API."""
    if cache.exists():
        return cache.read_text().split()
    urls, token = [], None
    while True:
        q = f"?list-type=2&prefix={urllib.parse.quote(PREFIX)}"
        if token:
            q += f"&continuation-token={urllib.parse.quote(token)}"
        # The proxy 403s urllib's default Python-urllib user agent.
        req = urllib.request.Request(BASE + "global-data/" + q,
                                     headers={"User-Agent": "curl/8.0"})
        xml = urllib.request.urlopen(req, timeout=60).read().decode()
        # Listed keys already include the "global-data/" repo prefix.
        urls += [BASE + k for k in re.findall(r"<Key>([^<]+\.parquet)</Key>", xml)]
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            break
        token = m.group(1)
    cache.write_text("\n".join(urls) + "\n")
    return urls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=Path.cwd())
    ap.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    ap.add_argument("--max-area-km2", type=float, default=None,
                    help="drop fields larger than this (e.g. 100)")
    args = ap.parse_args()

    staged = args.work / "staged"
    for y in args.years:
        (staged / str(y)).mkdir(parents=True, exist_ok=True)

    by_cc = defaultdict(list)
    for u in build_manifest(args.work / "manifest.txt"):
        m = re.search(r"country_code=([^/]+)/", u)
        if m:
            by_cc[m.group(1)].append(u)

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET TimeZone='UTC';")  # year markers are UTC midnights
    con.execute("SET memory_limit='48GB'; SET preserve_insertion_order=false; SET threads=16;")
    con.execute("SET http_retries=8; SET http_retry_wait_ms=2000; SET http_timeout=120000;")

    area_filter = ""
    if args.max_area_km2 is not None:
        area_filter = f'AND "metrics:area" <= {args.max_area_km2 * 1e6}'

    t0 = time.time()
    done = skipped = 0
    rows_total = defaultdict(int)
    for i, cc in enumerate(sorted(by_cc), 1):
        outs = {y: staged / str(y) / f"{cc}.parquet" for y in args.years}
        if all(o.exists() for o in outs.values()):
            skipped += 1
            continue
        # One network pass: spool all years for this country to local disk...
        spool = args.work / f"spool_{cc}.parquet"
        urls = ", ".join(f"'{u}'" for u in by_cc[cc])
        con.execute(f"""
            COPY (
              SELECT id,
                     "metrics:area"           AS area,
                     confidence,
                     "admin:country_code"     AS country,
                     "admin:subdivision_code" AS state,
                     date_part('year', "determination:datetime") AS yr,
                     geometry
              FROM read_parquet([{urls}], union_by_name=true)
              WHERE 1=1 {area_filter}
            ) TO '{spool}' (FORMAT PARQUET, COMPRESSION zstd, ROW_GROUP_SIZE 65536)
        """)
        # ...then split per year, deduping boundary-straddling rows (#9).
        for y, out in outs.items():
            if out.exists():
                continue
            tmp = out.with_suffix(".parquet.tmp")
            con.execute(f"""
                COPY (
                  SELECT area, confidence, country, state, geometry FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY id, area ORDER BY state NULLS LAST) AS rn
                    FROM '{spool}' WHERE yr = {y}
                  ) WHERE rn = 1
                ) TO '{tmp}' (FORMAT PARQUET, COMPRESSION zstd, ROW_GROUP_SIZE 65536)
            """)
            tmp.rename(out)
            n = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
            rows_total[y] += n
            print(f"[{i}/{len(by_cc)}] {cc} {y}: {n:,} rows, "
                  f"{out.stat().st_size / 1e6:.0f} MB "
                  f"({(time.time() - t0) / 60:.0f} min elapsed)", flush=True)
        spool.unlink()
        done += 1

    for y in args.years:
        print(f"{y}: {rows_total[y]:,} new rows", flush=True)
    print(f"staged {done} countries ({skipped} already present), "
          f"{(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
