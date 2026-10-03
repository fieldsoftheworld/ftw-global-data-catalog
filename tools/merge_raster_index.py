#!/usr/bin/env python3
"""Fold one year's index (``raster_{year}.parquet``) into ``raster.parquet``.

``tools/build_raster_index.py`` reads COG headers from a staging tree that is purged once a year
is uploaded, so a new year is indexed on its own and merged here. Rows of that year already in the
main index are replaced, rows are sorted by (year, tile_key) and the GeoParquet bbox is recomputed.

    python3 tools/merge_raster_index.py --year 2017 --main staging-data/index/raster.parquet
    # reads staging-data/index/raster_2017.parquet next to it
"""
import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent


def merge(main: Path, part: Path, year: int) -> pa.Table:
    base = pq.read_table(main)
    new = pq.read_table(part).select(base.schema.names).cast(base.schema)
    if set(pc.unique(new["year"]).to_pylist()) != {year}:
        raise SystemExit(f"{part} holds years other than {year}")
    keep = base.filter(pc.not_equal(base["year"], year))
    t = pa.concat_tables([keep, new]).sort_by([("year", "ascending"), ("tile_key", "ascending")])
    geo = json.loads(base.schema.metadata[b"geo"])
    col = geo["columns"][geo["primary_column"]]
    if "bbox" in col:
        col["bbox"] = [
            pc.min(t["xmin"]).as_py(),
            pc.min(t["ymin"]).as_py(),
            pc.max(t["xmax"]).as_py(),
            pc.max(t["ymax"]).as_py(),
        ]
    return t.replace_schema_metadata({**base.schema.metadata, b"geo": json.dumps(geo).encode()})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument(
        "--main", type=Path, default=ROOT / "staging-data" / "index" / "raster.parquet"
    )
    a = ap.parse_args()
    part = a.main.with_name(f"raster_{a.year}.parquet")
    t = merge(a.main, part, a.year)
    tmp = a.main.with_suffix(".parquet.tmp")
    pq.write_table(t, tmp, compression="zstd")
    tmp.replace(a.main)
    years = sorted(set(t["year"].to_pylist()))
    print(f"{a.main}: {t.num_rows} rows, years {years[0]}-{years[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
