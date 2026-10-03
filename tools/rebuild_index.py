#!/usr/bin/env python3
"""Rewrite index/vector.parquet hrefs for the canonical hive layout.

The manifest's ``href``/``s3_href`` columns point at the flat
``vector/{year}/utm{NN}.parquet`` layout. After the user-approved move to
``vector/{year}/zone=NN/utm{NN}.parquet`` this regenerates the manifest with
rewritten paths — every other column and the GeoParquet metadata are
preserved (DuckDB rewrite + ``gpio convert`` to restore the ``geo`` key).

    python3 tools/rebuild_index.py
    # writes staging-data/index/vector.parquet; upload with tools/upload_data.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "staging-data" / "index"
LIVE = "https://data.source.coop/ftw/global-data-2e/index/vector.parquet"
GPIO = "/u/cholmes/ftw-us-tiles/venv/bin/gpio"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plain = OUT_DIR / "vector_plain.parquet"
    out = OUT_DIR / "vector.parquet"
    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-global-data-catalog)"}
    )
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET http_retries=20;")
    con.execute(f"""
        COPY (
          SELECT * REPLACE (
            regexp_replace(href,
              '/vector/(\\d{{4}})/utm(\\d{{2}})\\.parquet$',
              '/vector/\\1/zone=\\2/utm\\2.parquet') AS href,
            regexp_replace(s3_href,
              '/vector/(\\d{{4}})/utm(\\d{{2}})\\.parquet$',
              '/vector/\\1/zone=\\2/utm\\2.parquet') AS s3_href
          )
          FROM read_parquet('{LIVE}')
        ) TO '{plain}' (FORMAT PARQUET, COMPRESSION zstd)
    """)
    subprocess.run(
        [GPIO, "convert", "geoparquet", str(plain), str(out),
         "--geoparquet-version", "2.0"],
        check=True,
    )
    plain.unlink()
    n, hive = con.execute(f"""
        SELECT count(*), count(*) FILTER (href LIKE '%/zone=%')
        FROM read_parquet('{out}')
    """).fetchone()
    print(f"{out}: {n} rows, {hive} hive hrefs")
    if n != hive:
        raise SystemExit("some hrefs did not rewrite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
