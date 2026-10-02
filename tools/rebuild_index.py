#!/usr/bin/env python3
"""Rewrite an index manifest's hrefs for the canonical layouts.

``vector``: the flat ``vector/{year}/utm{NN}.parquet`` hrefs become the hive
layout ``vector/{year}/zone=NN/utm{NN}.parquet`` (the 2026-09 move).
``raster``: the flat ``raster/{year}/{tile}.tif`` hrefs become the per-item
folders ``raster/{year}/{tile}/{tile}.tif`` (the 2026-10 move). Both rewrites
are idempotent — an href already in the target layout has an extra path
segment the pattern cannot match — and every other column and the GeoParquet
metadata are preserved (DuckDB rewrite + ``gpio convert`` to restore the
``geo`` key).

    python3 tools/rebuild_index.py raster
    # writes staging-data/index/raster.parquet; upload with tools/upload_data.py
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "staging-data" / "index"
LIVE_BASE = "https://data.source.coop/ftw/global-data-beta/index"
GPIO = "/u/cholmes/ftw-us-tiles/venv/bin/gpio"

# (flat-layout pattern, replacement, target-layout pattern). RE2 has no
# backreferences, so the target check matches the shape (one folder level
# between year and file), and main() spot-checks folder==stem in Python.
REWRITES = {
    "vector": (
        r"/vector/(\d{4})/utm(\d{2})\.parquet$",
        r"/vector/\1/zone=\2/utm\2.parquet",
        r"/vector/\d{4}/zone=\d{2}/utm\d{2}\.parquet$",
    ),
    # Phase-2 relayout: per-item folders -> unified grouped hierarchy.
    # RE2 has no backreferences, so the tile key is rebuilt from its parts:
    # \2 = zone digits, \3 = band letter, \4 = the square/offset suffix.
    "raster": (
        r"/raster/(\d{4})/(\d{2})([A-Z])([A-Z0-9]+_\d+_\d+)/[^/]+\.tif$",
        r"/raster/\1/zone=\2/gzd=\2\3/\2\3\4/\2\3\4.tif",
        r"/raster/\d{4}/zone=\d{2}/gzd=\d{2}[A-Z]/[A-Z0-9_]+/[A-Z0-9_]+\.tif$",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("which", choices=sorted(REWRITES))
    args = parser.parse_args()
    flat, repl, target = REWRITES[args.which]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plain = OUT_DIR / f"{args.which}_plain.parquet"
    out = OUT_DIR / f"{args.which}.parquet"
    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-global-data-catalog)"}
    )
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET http_retries=20;")
    con.execute(f"""
        COPY (
          SELECT * REPLACE (
            regexp_replace(href, '{flat}', '{repl}') AS href,
            regexp_replace(s3_href, '{flat}', '{repl}') AS s3_href
          )
          FROM read_parquet('{LIVE_BASE}/{args.which}.parquet')
        ) TO '{plain}' (FORMAT PARQUET, COMPRESSION zstd)
    """)
    subprocess.run(
        [GPIO, "convert", "geoparquet", str(plain), str(out),
         "--geoparquet-version", "2.0"],
        check=True,
    )
    plain.unlink()
    n, ok = con.execute(f"""
        SELECT count(*), count(*) FILTER (regexp_matches(href, '{target}'))
        FROM read_parquet('{out}')
    """).fetchone()
    print(f"{out}: {n} rows, {ok} in target layout")
    if n != ok:
        raise SystemExit("some hrefs did not rewrite")
    for href, in con.execute(
        f"SELECT href FROM read_parquet('{out}') USING SAMPLE 5"
    ).fetchall():
        parts = href.rsplit("/", 2)
        stem = parts[2].split(".")[0]
        if args.which == "raster" and parts[1] != stem:
            raise SystemExit(f"folder != stem: {href}")
        print(f"  ok {href}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
