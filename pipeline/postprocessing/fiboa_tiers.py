"""Slurm sizes for whole-zone fiboa conversion, from the zone's merged row count.

Measured on 2025 with 4 CPUs and 28 GB: 2.1-2.5 cores in use at any size; MaxRSS 0.7 GB at
285k rows, 6 GB at 880k and up to 24 GB at 2.2M, depending on how dense the seam strips are.

    python fiboa_tiers.py 2025 --in-root merged [--update JOBID]   # resize pending array tasks
"""

import argparse
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from fiboa_common import discover_zones

TIERS = (  # name, CPUs, Slurm memory, DuckDB memory limit, rows below
    ("xs", 2, "4G", "2GB", 300_000),
    ("s", 3, "12G", "8GB", 1_000_000),
    ("m", 3, "28G", "16GB", 3_000_000),
    ("l", 4, "28G", "16GB", 10**12),
)


def tier(rows: int) -> tuple[str, int, str, str, int]:
    return next(t for t in TIERS if rows < t[4])


def tiers(in_root: Path, year: int) -> dict[str, list[int]]:
    """Zone indexes (into the year's sorted zone list) per tier."""
    out: dict[str, list[int]] = {t[0]: [] for t in TIERS}
    for i, z in enumerate(discover_zones(in_root, year)):
        n = pq.ParquetFile(in_root / str(year) / f"zone={z}" / "part-0.parquet").metadata.num_rows
        out[tier(n)[0]].append(i)
    return out


def slurm(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        sys.exit(f"{' '.join(cmd)} failed ({e.returncode}): {e.stderr.strip()}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("year", type=int)
    ap.add_argument("--in-root", type=Path, default=Path("merged"))
    ap.add_argument("--update", metavar="JOBID", help="resize this array's pending tasks in place")
    a = ap.parse_args(argv)
    by_tier = tiers(a.in_root, a.year)
    pending = set()
    if a.update:
        pending = set(slurm(["squeue", "-h", "-t", "PD", "-j", a.update, "-r", "-o", "%K"]).split())
    for name, cpus, mem, duck, _ in TIERS:
        idx = by_tier[name]
        for i in (i for i in idx if str(i) in pending):
            slurm(["scontrol", "update", f"JobId={a.update}_{i}", f"NumCPUs={cpus}",
                   f"MinMemoryNode={int(mem[:-1]) * 1024}"])
        if idx:
            print(name, cpus, mem, duck, ",".join(map(str, idx)))


if __name__ == "__main__":
    main()
