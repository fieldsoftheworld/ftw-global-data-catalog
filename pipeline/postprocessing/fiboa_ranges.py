"""``fiboa_convert.convert`` split into ranges, so no one task holds a whole zone.

  plan    per zone: the seam union once (strip parcels in final form, sorted) and the
          other source rows hashed into ``--ranges`` buckets (``work/src/bucket=k``)
  range   per (zone, k): final form of one bucket, sorted by (hk, id), as ``part-k.parquet``
  stitch  per zone: merges the sorted parts and the strip rows on (hk, id) and writes
          ``zone={zone}/utm{zone}.parquet`` with ``fiboa_common.write_sorted``
  submit  per year: sbatch chains plan -> range array -> stitch, or one ``fiboa_convert``
          job for a zone under ``--rows-per-range`` rows
  local   the same on one machine, each phase on a process pool

The final form of a row depends only on that row, and the merge restores the global
(hk, id) order, so the output is byte-identical to ``fiboa_convert.convert``: the same
rows, order, row groups and footer.

    python fiboa_ranges.py submit --year 2025 --in-root merged --out-root fiboa \\
        --land-polygons land.parquet
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
import fiboa_convert as fc
from fiboa_common import connect, discover_zones, final_select, strip_rows_sql, write_sorted
from fiboa_tiers import tier
from sea_filter import SeaFilter

HERE = Path(__file__).resolve().parent
ROWS_PER_RANGE = 400_000
RAW_COLS = "tile_key, parcel_id, pf_mean, geometry"


def work_dir(a: argparse.Namespace) -> Path:
    return a.work_root / str(a.year) / a.zone


def done_path(wd: Path, k: int) -> Path:
    return wd / f"done-{k:04d}.json"


def part_path(wd: Path, k: int) -> Path:
    return wd / f"part-{k:04d}.parquet"


def plan(a: argparse.Namespace) -> None:
    cid = f"ftw-s2-{a.year}"
    src = fc.IN_ROOT / str(a.year) / f"zone={a.zone}" / "part-0.parquet"
    wd = work_dir(a)
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)
    max_m2 = float(fc.merge_summary(a.year).get("max_km2", fc.MAX_PARCEL_M2 / 1e6)) * 1e6
    duck = fc.TMP_ROOT / f"plan-{a.year}-{a.zone}-{os.getpid()}"
    con = connect(a.threads, a.memory_limit, duck)
    try:
        fc.require_geo_metadata(src)
        bbox, n_in = fc.zone_extent(con, src, a.zone, a.year)
        fc.join_seams(con, src)
        strip = wd / "strip.parquet"
        con.sql(
            f"COPY ({final_select(strip_rows_sql(), cid, a.year, bbox, max_m2)} ORDER BY hk, id) "
            f"TO '{strip}' (FORMAT parquet, COMPRESSION zstd)"
        )
        con.sql(
            f"COPY (SELECT {RAW_COLS}, hash(tile_key, parcel_id) % {a.ranges} AS bucket "
            f"FROM read_parquet('{src}') WHERE NOT {fc.IN_STRIP}) TO '{wd / 'src'}' "
            f"(FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 1, PARTITION_BY (bucket))"
        )
        n_raw = con.sql(
            f"SELECT count(*) FROM read_parquet('{src}') WHERE NOT {fc.IN_STRIP}"
        ).fetchall()[0][0]
    finally:
        con.close()
        shutil.rmtree(duck, ignore_errors=True)
    raw_rows = [0] * a.ranges
    for f in (wd / "src").glob("bucket=*/*.parquet"):
        raw_rows[int(f.parent.name.split("=")[1])] += pq.ParquetFile(f).metadata.num_rows
    if sum(raw_rows) != n_raw:
        sys.exit(f"plan partitioned {sum(raw_rows)} of {n_raw} source rows")
    record = {"bbox": bbox, "n_in": n_in, "max_m2": max_m2, "ranges": a.ranges,
              "raw_rows": raw_rows, "strip_rows": pq.ParquetFile(strip).metadata.num_rows}
    (wd / "plan.json").write_text(json.dumps(record))
    print(f"plan {a.year} zone {a.zone}: {n_in:,} rows, {record['strip_rows']:,} strip "
          f"parcels, {a.ranges} ranges", flush=True)


def range_task(a: argparse.Namespace) -> None:
    cid = f"ftw-s2-{a.year}"
    wd = work_dir(a)
    pj = json.loads((wd / "plan.json").read_text())
    done_path(wd, a.k).unlink(missing_ok=True)
    dst = part_path(wd, a.k)
    dst.unlink(missing_ok=True)
    files = list((wd / "src" / f"bucket={a.k}").glob("*.parquet"))
    raw_n = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    if raw_n != pj["raw_rows"][a.k]:
        sys.exit(f"range {a.k}: {raw_n} raw rows on disk, the plan has {pj['raw_rows'][a.k]}")
    n = 0
    if files:
        duck = fc.TMP_ROOT / f"range-{a.year}-{a.zone}-{a.k}-{os.getpid()}"
        con = connect(a.threads, a.memory_limit, duck)
        try:
            rows = fc.repaired(wd / "src" / f"bucket={a.k}" / "*.parquet", "true")
            sql = f"{final_select(rows, cid, a.year, pj['bbox'], pj['max_m2'])} ORDER BY hk, id"
            con.sql(f"COPY ({sql}) TO '{dst}' (FORMAT parquet, COMPRESSION zstd)")
        finally:
            con.close()
            shutil.rmtree(duck, ignore_errors=True)
        n = pq.ParquetFile(dst).metadata.num_rows
    tmp = done_path(wd, a.k).with_suffix(".tmp")
    tmp.write_text(json.dumps({"rows": n, "raw_rows": raw_n}))
    os.replace(tmp, done_path(wd, a.k))
    print(f"range {a.k}: {n:,} rows from {raw_n:,}", flush=True)


def sorted_streams(wd: Path, pj: dict) -> list[Path]:
    """The strip rows and every range's part, each sorted by (hk, id); checked against the plan."""
    problems, streams = [], [wd / "strip.parquet"]
    if pq.ParquetFile(streams[0]).metadata.num_rows != pj["strip_rows"]:
        problems.append("strip rows differ from the plan")
    for k in range(pj["ranges"]):
        if not done_path(wd, k).exists():
            problems.append(f"range {k}: no done record")
            continue
        rec = json.loads(done_path(wd, k).read_text())
        if rec["raw_rows"] != pj["raw_rows"][k]:
            problems.append(f"range {k}: done record does not match the plan")
        part = part_path(wd, k)
        if rec["rows"] and not part.exists():
            problems.append(f"range {k}: no part for {rec['rows']} rows")
        elif rec["rows"] and pq.ParquetFile(part).metadata.num_rows != rec["rows"]:
            problems.append(f"range {k}: part rows differ from the done record")
        elif rec["rows"]:
            streams.append(part)
    if problems:
        sys.exit("; ".join(problems))
    return streams


def merged(streams: list[Path], batch: int) -> Iterator[pa.RecordBatch]:
    """Rows of ``streams`` (each sorted by (hk, id)) in global (hk, id) order.

    Only the keys of every row are held; the rows themselves are read a batch at a time.
    Each stream is consumed in its own order, so the rows a chunk takes from a stream are
    that stream's next ones.
    """
    # one nullable schema: DuckDB 2.0 marks some columns NOT NULL in one stream and not another
    schema = pa.schema(f.with_nullable(True) for f in pq.read_schema(streams[0]))
    keys = pa.concat_tables(
        pa.table({
            "hk": pq.read_table(p, columns=["hk"])["hk"].cast(schema.field("hk").type),
            "id": pq.read_table(p, columns=["id"])["id"].cast(pa.string()),
            "s": pa.array(np.full(pq.ParquetFile(p).metadata.num_rows, i, np.int32)),
        })
        for i, p in enumerate(streams)
    )
    order = pc.sort_indices(keys, sort_keys=[("hk", "ascending"), ("id", "ascending")])
    src = keys.column("s").take(order).to_numpy()
    del keys, order
    readers = [pq.ParquetFile(p).iter_batches(batch_size=batch) for p in streams]
    buffers: list[pa.Table | None] = [None] * len(streams)

    def take(i: int, n: int) -> pa.Table:
        got, need = [], n
        while need:
            if buffers[i] is None or buffers[i].num_rows == 0:
                buffers[i] = pa.Table.from_batches([next(readers[i])]).cast(schema)
            piece = buffers[i].slice(0, need)
            buffers[i] = buffers[i].slice(piece.num_rows)
            got.append(piece)
            need -= piece.num_rows
        return pa.concat_tables(got)

    for start in range(0, len(src), batch):
        s = src[start : start + batch]
        counts = np.bincount(s, minlength=len(streams))
        tables = [take(i, int(c)) for i, c in enumerate(counts) if c]
        chunk = pa.concat_tables(tables).combine_chunks()
        offsets = np.concatenate(([0], np.cumsum(counts)[:-1]))
        rank = np.empty(len(s), np.int64)
        for i in np.flatnonzero(counts):
            rank[s == i] = np.arange(counts[i])
        yield from chunk.take(pa.array(offsets[s] + rank)).to_batches()


def stitch_task(a: argparse.Namespace) -> None:
    cid = f"ftw-s2-{a.year}"
    wd = work_dir(a)
    pj = json.loads((wd / "plan.json").read_text())
    streams = sorted_streams(wd, pj)
    dst = a.out_root / str(a.year) / f"zone={a.zone}" / f"utm{a.zone}.parquet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    land = None if a.no_sea_filter else a.land_polygons
    schema = fc.file_schema(cid, a.year, pj["bbox"], fc.merge_summary(a.year), a.spec,
                            land is not None)
    reader = merged(streams, fc.ROW_GROUP)
    sea = SeaFilter(land, int(a.zone)) if land is not None else None
    n = write_sorted(sea(reader) if sea else reader, schema, dst, fc.ROW_GROUP,
                     validate=fc._validator(a.zone, pj["n_in"]))
    if not a.keep:
        shutil.rmtree(wd, ignore_errors=True)
    dropped = f", {sea.dropped:,} sea parcels dropped" if sea else ""
    print(f"{dst.name}: {n:,} features from {pj['n_in']:,} rows{dropped}", flush=True)


def _zones(a: argparse.Namespace) -> list[tuple[str, int]]:
    zones = discover_zones(fc.IN_ROOT, a.year)
    unknown = sorted(set(a.zones or []) - set(zones))
    if unknown:
        sys.exit(f"zones not in the {a.year} source: {unknown}")
    out = []
    for z in zones:
        if a.zones and z not in a.zones:
            continue
        src = fc.IN_ROOT / str(a.year) / f"zone={z}" / "part-0.parquet"
        out.append((z, pq.ParquetFile(src).metadata.num_rows))
    return out


def _common(a: argparse.Namespace, zone: str) -> list[str]:
    args = ["--year", str(a.year), "--zone", zone, "--in-root", str(fc.IN_ROOT),
            "--tmp-dir", str(fc.TMP_ROOT), "--work-root", str(a.work_root)]
    return args


def _publish_args(a: argparse.Namespace) -> list[str]:
    out = ["--out-root", str(a.out_root)]
    out += ["--no-sea-filter"] if a.no_sea_filter else ["--land-polygons", str(a.land_polygons)]
    return out + (["--spec", a.spec] if a.spec else [])


def _single(a: argparse.Namespace, zone: str, rows: int) -> tuple[list[str], tuple]:
    t = tier(rows)
    cmd = ["fiboa_convert.py", "--year", str(a.year), "--zone", zone, "--in-root",
           str(fc.IN_ROOT), "--tmp-dir", str(fc.TMP_ROOT), "--threads", str(t[1]),
           "--memory-limit", t[3], *_publish_args(a)]
    return cmd, t


def submit(a: argparse.Namespace) -> None:
    """Accounts come from ``SBATCH_ACCOUNT`` (sbatch reads it), not from this script."""
    base = ["sbatch", "--parsable", f"--partition={a.partition}", "--time=04:00:00"]
    if a.exclude:
        base.append(f"--exclude={a.exclude}")
    py = f"cd {HERE} && {sys.executable} -u -W ignore"

    def sbatch(name: str, cpus: int, mem: str, cmd: list[str], *extra: str) -> str:
        args = [*base, f"--cpus-per-task={cpus}", f"--mem={mem}", f"--job-name={name}",
                f"--output={a.log_dir}/{name}_%j.out", *extra, "--wrap", f"{py} {' '.join(cmd)}"]
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()

    a.log_dir.mkdir(parents=True, exist_ok=True)
    for z, rows in _zones(a):
        k = max(1, round(rows / a.rows_per_range))
        if k == 1:
            cmd, t = _single(a, z, rows)
            print(z, rows, "single", sbatch(f"fiboa-{z}", t[1], t[2], cmd))
            continue
        me = ["fiboa_ranges.py"]
        p = sbatch(f"fiboa-plan-{z}", 4, a.plan_mem,
                   [*me, "plan", *_common(a, z), "--ranges", str(k), "--threads", "4",
                    "--memory-limit", a.plan_duck])
        r = sbatch(f"fiboa-range-{z}", a.range_cpus, a.range_mem,
                   [*me, "range", *_common(a, z), "--k", "$SLURM_ARRAY_TASK_ID", "--threads",
                    str(a.range_cpus), "--memory-limit", a.range_duck],
                   f"--dependency=afterok:{p}", f"--array=0-{k - 1}%{a.throttle}")
        s = sbatch(f"fiboa-stitch-{z}", 1, a.stitch_mem,
                   [*me, "stitch", *_common(a, z), *_publish_args(a)],
                   f"--dependency=afterok:{r}")
        print(z, rows, f"K={k}", p, r, s)


def local(a: argparse.Namespace) -> None:
    py = [sys.executable, "-u", "-W", "ignore"]
    phases: list[list[list[str]]] = [[], [], []]
    for z, rows in _zones(a):
        k = max(1, round(rows / a.rows_per_range))
        if k == 1:
            phases[0].append([*py, *_single(a, z, rows)[0]])
            continue
        me = [*py, "fiboa_ranges.py"]
        phases[0].append([*me, "plan", *_common(a, z), "--ranges", str(k), "--threads", "4",
                          "--memory-limit", a.plan_duck])
        phases[1] += [[*me, "range", *_common(a, z), "--k", str(i), "--threads", "3",
                       "--memory-limit", a.range_duck] for i in range(k)]
        phases[2].append([*me, "stitch", *_common(a, z), *_publish_args(a)])

    def run(cmd: list[str]) -> int:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, check=False)
        if r.returncode:
            print(f"FAILED ({r.returncode}): {' '.join(cmd[4:])}\n{r.stderr[-2000:]}", flush=True)
        return r.returncode

    for i, (cmds, jobs) in enumerate(zip(phases, (a.jobs // 4, a.jobs // 3, a.jobs), strict=True)):
        with ThreadPoolExecutor(max(1, jobs)) as ex:
            failed = sum(1 for rc in ex.map(run, cmds) if rc)
        print(f"phase {i}: {len(cmds) - failed}/{len(cmds)} ok", flush=True)
        if failed:
            sys.exit(f"{failed} task(s) failed in phase {i}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "range", "stitch", "submit", "local"):
        p = sub.add_parser(name)
        p.add_argument("--year", type=int, required=True)
        p.add_argument("--in-root", type=Path, default=fc.IN_ROOT)
        p.add_argument("--tmp-dir", type=Path, default=fc.TMP_ROOT)
        p.add_argument("--work-root", type=Path, default=Path("scratch/fiboa-ranges"))
        if name in ("plan", "range", "stitch"):
            p.add_argument("--zone", required=True)
        if name in ("plan", "range"):
            p.add_argument("--threads", type=int, default=4)
            p.add_argument("--memory-limit", default="8GB")
        if name == "plan":
            p.add_argument("--ranges", type=int, required=True)
        if name == "range":
            p.add_argument("--k", type=int, required=True)
        if name in ("stitch", "submit", "local"):
            p.add_argument("--out-root", type=Path, default=fc.OUT_ROOT)
            p.add_argument("--spec", help="see fiboa_convert.py --spec")
            p.add_argument("--keep", action="store_true", help="keep the work directory")
            sea = p.add_mutually_exclusive_group()
            sea.add_argument("--land-polygons", type=Path,
                             default=os.environ.get("FTW_LAND_POLYGONS"))
            sea.add_argument("--no-sea-filter", action="store_true")
        if name in ("submit", "local"):
            p.add_argument("--zones", nargs="*")
            p.add_argument("--rows-per-range", type=int, default=ROWS_PER_RANGE)
            p.add_argument("--plan-duck", default="14GB")
            p.add_argument("--range-duck", default="5GB")
        if name == "submit":
            p.add_argument("--partition", default="cpu")
            p.add_argument("--exclude", help="nodes to keep free, e.g. GPU nodes")
            p.add_argument("--log-dir", type=Path, default=Path("logs"))
            p.add_argument("--plan-mem", default="24G")
            p.add_argument("--range-cpus", type=int, default=3)
            p.add_argument("--range-mem", default="8G")
            p.add_argument("--stitch-mem", default="8G")
            p.add_argument("--throttle", type=int, default=32)
        if name == "local":
            p.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    a = ap.parse_args()
    fc.IN_ROOT, fc.TMP_ROOT = a.in_root.resolve(), a.tmp_dir.resolve()
    if a.cmd in ("stitch", "submit", "local"):
        a.out_root = a.out_root.resolve()
        if not a.no_sea_filter and a.land_polygons is None:
            ap.error("pass --land-polygons (or set FTW_LAND_POLYGONS), or --no-sea-filter")
    a.work_root = a.work_root.resolve()
    {"plan": plan, "range": range_task, "stitch": stitch_task, "submit": submit,
     "local": local}[a.cmd](a)


if __name__ == "__main__":
    main()
