#!/usr/bin/env python3
"""Fold one year's index (``raster_{year}.parquet``) into ``raster.parquet``.

``tools/build_raster_index.py`` reads COG headers from a staging tree that is purged once a year
is uploaded, so a new year is indexed on its own and merged here. Rows of that year already in the
main index are replaced, rows are sorted by (year, tile_key) and the GeoParquet bbox is recomputed
over the merged rows. A main index that does not exist yet is treated as empty, so the first merge
bootstraps it from the part -- that is how a from-scratch rebuild of all nine years starts.

Replacing a year with fewer rows than it had is refused: the published catalog tells readers to
enumerate tiles from this index and never by listing the bucket, so a part built over a partly
purged staging tree would make published COGs unreachable. Pass ``--allow-shrink`` when the year
really did lose tiles. The previous index is kept as ``raster.parquet.bak``.

    python3 tools/merge_raster_index.py --year 2017 --main staging-data/index/raster.parquet
    # reads staging-data/index/.parts/raster_2017.parquet, or --part
"""
import argparse
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent


def merge(main: Path, part: Path, year: int, allow_shrink: bool = False) -> tuple[pa.Table, int]:
    "(merged table, rows of ``year`` replaced). A missing ``main`` is an empty base."
    if main.exists():
        base = pq.read_table(main)
        new = pq.read_table(part).select(base.schema.names).cast(base.schema)
    else:
        new = pq.read_table(part)
        base = new.slice(0, 0)
    if set(pc.unique(new["year"]).to_pylist()) != {year}:
        raise SystemExit(f"{part} holds years other than {year}")
    same_year = pc.equal(base["year"], year)
    replaced = base.filter(same_year).num_rows
    if replaced and new.num_rows < replaced and not allow_shrink:
        raise SystemExit(
            f"{part} holds {new.num_rows:,} rows for {year}, fewer than the {replaced:,} "
            f"already in {main}; rebuild over the full staging tree or pass --allow-shrink"
        )
    keep = base.filter(pc.invert(same_year))
    t = pa.concat_tables([keep, new]).sort_by([("year", "ascending"), ("tile_key", "ascending")])
    geo = json.loads(base.schema.metadata[b"geo"])
    geo["columns"][geo["primary_column"]]["bbox"] = [
        pc.min(t["xmin"]).as_py(),
        pc.min(t["ymin"]).as_py(),
        pc.max(t["xmax"]).as_py(),
        pc.max(t["ymax"]).as_py(),
    ]
    meta = {**base.schema.metadata, b"geo": json.dumps(geo).encode()}
    return t.replace_schema_metadata(meta), replaced


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument(
        "--main", type=Path, default=ROOT / "staging-data" / "index" / "raster.parquet"
    )
    ap.add_argument("--part", type=Path, help="default: {main-dir}/.parts/raster_{year}.parquet")
    ap.add_argument("--allow-shrink", action="store_true", help="accept fewer rows for the year")
    a = ap.parse_args()
    part = a.part or a.main.parent / ".parts" / f"raster_{a.year}.parquet"
    if not part.exists():
        raise SystemExit(f"no part for {a.year}: {part}")
    t, replaced = merge(a.main, part, a.year, a.allow_shrink)
    a.main.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.main.with_suffix(".parquet.tmp")
    pq.write_table(t, tmp, compression="zstd")
    if a.main.exists():
        # .bak is not in upload_data.py's suffix allow-list, so the copy stays local
        shutil.copy2(a.main, a.main.with_name(a.main.name + ".bak"))
    tmp.replace(a.main)
    years = sorted(set(t["year"].to_pylist()))
    added = t.filter(pc.equal(t["year"], a.year)).num_rows
    print(f"{a.year}: replaced {replaced:,} rows with {added:,}")
    print(f"{a.main}: {t.num_rows} rows, years {years[0]}-{years[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
