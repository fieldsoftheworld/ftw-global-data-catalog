#!/usr/bin/env python3
"""Tile indexes for ``outlines.py`` on EC2, reading B04 from the Source Coop bucket.

``outlines.py`` counts nodata quarters from each tile's four B04 mosaics, which it opens at
``b04_s3_href`` against ``b04_s3_endpoint`` (``context.py``). The indexes ``indexes.py``
builds carry only the ``https://data.source.coop/`` hrefs, so this adds the two columns for the
same objects in the Source Coop bucket in us-west-2, which EC2 reads without credentials
(``AWS_NO_SIGN_REQUEST=YES``) and without a transfer charge.

    python qa_index.py INDEX_DIR OUT_DIR    # every tile_index_*.parquet
"""

import argparse
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

HTTPS = "https://data.source.coop/"
BUCKET = "us-west-2.opendata.source.coop"
ENDPOINT = "https://s3.us-west-2.amazonaws.com"


def with_s3_hrefs(table: pa.Table) -> pa.Table:
    """``table`` plus ``b04_s3_href`` (s3://BUCKET/key) and ``b04_s3_endpoint``."""
    href = table["b04_href"]
    if not pc.all(pc.starts_with(href, HTTPS)).as_py():
        raise ValueError(f"every b04_href must start with {HTTPS}")
    s3 = pc.replace_substring(href, HTTPS, f"s3://{BUCKET}/", max_replacements=1)
    for name in ("b04_s3_href", "b04_s3_endpoint"):
        if name in table.column_names:
            table = table.drop_columns(name)
    endpoint = pa.array([ENDPOINT] * table.num_rows, pa.string())
    return table.append_column("b04_s3_href", s3).append_column("b04_s3_endpoint", endpoint)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("index_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    a = ap.parse_args()
    paths = sorted(a.index_dir.glob("tile_index_*.parquet"))
    if not paths:
        ap.error(f"no tile_index_*.parquet in {a.index_dir}")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for p in paths:
        t = with_s3_hrefs(pq.read_table(p))
        pq.write_table(t, a.out_dir / p.name)
        print(f"{p.name}: {t.num_rows} rows")


if __name__ == "__main__":
    main()
