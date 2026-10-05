"""Validate fiboa zone files before publication, for one or more years.

Checks per file: the nine-column schema, ZSTD on every column, row-group size,
GeoParquet CRS metadata and (sampled) geometry validity, null ids, positive area and
score in 0-100. Parquet does not record the zstd level, so level 19 is not checked.
Per year it also checks the hive layout and the zone-file count.

    python validate_vector.py --root fiboa 2024 2025 [--sample 200] [--expect-zones 54]
"""

import argparse
import json
import random
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely

COLUMNS = [
    "id",
    "collection",
    "geometry",
    "bbox",
    "metrics:area",
    "metrics:perimeter",
    "score",
    "determination:datetime",
    "determination:method",
]
MAX_RG = 8192 * 2


def check_file(path: Path, sample: int, rng: random.Random) -> tuple[int, int, list[str]]:
    "Returns (rows, row groups, problems) for one zone file."
    errs = []
    f = pq.ParquetFile(path)
    md = f.metadata
    schema_ok = f.schema_arrow.names == COLUMNS
    if not schema_ok:
        errs.append(f"columns {f.schema_arrow.names}")
    for rg in range(md.num_row_groups):
        r = md.row_group(rg)
        if r.num_rows > MAX_RG:
            errs.append(f"row group {rg} has {r.num_rows} rows")
        bad = {r.column(i).compression for i in range(md.num_columns)} - {"ZSTD"}
        if bad:
            errs.append(f"rg {rg} compression {bad}")
    geo = json.loads((f.schema_arrow.metadata or {}).get(b"geo", b"{}"))
    if not geo.get("columns", {}).get("geometry", {}).get("crs"):
        errs.append("no geometry crs in geo metadata")
    # Only sample when the columns are the ones below: schema drift is the likeliest
    # real defect, and reading named columns out of a drifted file raises instead of
    # reporting it, losing this file's problems and every later file's.
    if md.num_rows and schema_ok:
        rg = rng.randrange(md.num_row_groups)
        t = f.read_row_group(rg, columns=["id", "geometry", "metrics:area", "score"])
        n = min(sample, t.num_rows)
        t = t.take(sorted(rng.sample(range(t.num_rows), n)))
        geoms = shapely.from_wkb(t["geometry"].to_pylist())
        invalid = int((~shapely.is_valid(geoms)).sum())
        empty = int(shapely.is_empty(geoms).sum())
        if invalid or empty:
            errs.append(f"sample: {invalid} invalid, {empty} empty of {n}")
        # metrics:area is nullable, and a null arrives here as NaN, for which
        # `area <= 0` is False -- so test the positive case and negate it.
        area = t["metrics:area"].to_numpy(zero_copy_only=False)
        if not (area > 0).all():
            errs.append("non-positive or null area in sample")
        sc = pc.min_max(t["score"]).as_py()
        if sc["min"] is not None and not (sc["min"] >= 0 and sc["max"] <= 100):
            errs.append(f"score range {sc}")
        if t["id"].null_count:
            errs.append("null ids")
    return md.num_rows, md.num_row_groups, errs


def check_year(root: Path, year: str, sample: int, expect_zones: int, rng: random.Random):
    "Returns (files, rows, row groups, bytes, problems) for one year."
    files = sorted(Path(root, year).glob("zone=*/utm*.parquet"))
    rows = groups = nbytes = 0
    errs = []
    if len(files) != expect_zones:
        errs.append(f"{len(files)} zone files, expected {expect_zones}")
    for p in files:
        if p.parent.name != f"zone={p.stem[3:]}":
            errs.append(f"{p.relative_to(root)}: not in its hive dir")
        try:
            n, g, e = check_file(p, sample, rng)
        except Exception as exc:  # an unreadable file is a problem, not the end of the run
            errs.append(f"{p.relative_to(root)}: unreadable: {exc!r}")
            continue
        rows, groups, nbytes = rows + n, groups + g, nbytes + p.stat().st_size
        errs += [f"{p.relative_to(root)}: {x}" for x in e]
    return len(files), rows, groups, nbytes, errs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("years", nargs="+")
    ap.add_argument("--root", required=True, help="{root}/{year}/zone=NN/utmNN.parquet")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--expect-zones", type=int, default=54, help="zone files per year")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    failed = False
    for y in a.years:
        nfiles, rows, groups, nbytes, errs = check_year(
            Path(a.root), y, a.sample, a.expect_zones, rng
        )
        print(
            f"{y}: {nfiles} files, {rows:,} rows, {groups} row groups, {nbytes / 1e9:.1f} GB, "
            f"{nbytes / max(rows, 1):.0f} B/row -- {'OK' if not errs else f'{len(errs)} problems'}"
        )
        for e in errs[:20]:
            print("  ", e)
        failed |= bool(errs)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
