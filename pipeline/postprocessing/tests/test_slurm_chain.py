"""run_year.sh and fiboa.sbatch submit the postprocessing chain; sbatch is a stub that records
the calls, and simplify_polygons.by_size picks the size class of a simplify array."""

import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from simplify_polygons import by_size

STUB = """#!/bin/bash
n=$(cat "$STUB_DIR/count" 2>/dev/null || echo 100); echo $((n + 1)) > "$STUB_DIR/count"
echo "$* NSHARDS=${NSHARDS:-} MIN_MB=${MIN_MB:-} MAX_MB=${MAX_MB:-} TILES=${TILES:-}" \\
  >> "$STUB_DIR/calls.log"
echo $((n + 1))
"""


def executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def stub_env(tmp_path: Path) -> dict[str, str]:
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    executable(bin_ / "sbatch", STUB)
    keep = {k: v for k, v in os.environ.items() if not k.startswith(("SBATCH_", "SLURM_"))}
    return {**keep, "PATH": f"{bin_}:{os.environ['PATH']}", "STUB_DIR": str(tmp_path)}


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def run_year(tmp_path: Path, tiles: str, empty: str | None = None, **env: str):
    (tmp_path / "tiles.txt").write_text(tiles)
    (tmp_path / "land.parquet").touch()
    base = stub_env(tmp_path) | {
        "OUT": str(tmp_path / "run"),
        "SCORES": str(tmp_path),
        "INDEX_DIR": str(tmp_path),
        "TILES": str(tmp_path / "tiles.txt"),
        "FTW_LAND_POLYGONS": str(tmp_path / "land.parquet"),
    }
    if empty is not None:
        (tmp_path / "empty.txt").write_text(empty)
        base["EMPTY"] = str(tmp_path / "empty.txt")
    return subprocess.run(
        ["bash", str(HERE / "run_year.sh"), "2025"],
        capture_output=True, text=True, check=False, env=base | env,
    )  # fmt: skip


def job(lines: list[str], name: str) -> str:
    (line,) = [c for c in lines if f"-J {name} " in c]
    return line


def dependency(line: str) -> list[str]:
    m = re.search(r"--dependency=afterok:(\S+)", line)
    return m.group(1).split(":") if m else []


def job_id(lines: list[str], name: str) -> str:
    return str(101 + lines.index(job(lines, name)))


def test_chain_order_and_dependencies(tmp_path):
    tiles = "".join(f"31UFS_{i}_0\n" for i in range(25))
    r = run_year(tmp_path, tiles + "\n31UFS_0_0\n", TILES_PER_TASK="10", THROTTLE="4")
    assert r.returncode == 0, r.stderr
    lines = calls(tmp_path)
    outl = job(lines, "pp-outlines-2025")
    assert "--array=0-2%4" in outl and "NSHARDS=3 " in outl
    listed = tmp_path / "run/lists/outlines_2025.txt"
    assert f"TILES={listed}" in outl
    assert listed.read_text().split() == sorted(f"31UFS_{i}_0" for i in range(25))
    simp = [job(lines, f"pp-simplify-{c}-2025") for c in "sml"]
    assert [dependency(s) for s in simp] == [[job_id(lines, "pp-outlines-2025")]] * 3
    assert [re.search(r"MIN_MB=(\S*) MAX_MB=(\S*)", s).groups() for s in simp] == [
        ("0", "40"), ("40", "200"), ("200", "inf")
    ]
    assert [re.search(r"--mem=(\S+)", s).group(1) for s in simp] == ["2G", "8G", "40G"]
    merge = job(lines, "pp-merge-2025")
    assert dependency(merge) == [job_id(lines, f"pp-simplify-{c}-2025") for c in "sml"]
    assert f"TILES={tmp_path / 'tiles.txt'}" in merge
    assert dependency(job(lines, "pp-fiboa-2025")) == [job_id(lines, "pp-merge-2025")]
    assert len(lines) == 6 and not any("qa_vector" in c for c in lines)
    assert all("--kill-on-invalid-dep=yes" in c and "--export=ALL" in c for c in lines)
    assert not any("--account" in c or "--exclude" in c or "--partition" in c for c in lines)


def test_tiles_without_scores_are_not_outlined(tmp_path):
    r = run_year(tmp_path, "15TVG_0_0\n36TVT_0_0\n52RGP_0_0\n", empty="52RGP_0_0\n\n")
    assert r.returncode == 0, r.stderr
    listed = (tmp_path / "run/lists/outlines_2025.txt").read_text().split()
    assert listed == ["15TVG_0_0", "36TVT_0_0"]
    assert "--array=0-0%50" in job(calls(tmp_path), "pp-outlines-2025")


def test_exclude_and_after(tmp_path):
    r = run_year(tmp_path, "15TVG_0_0\n", EXCLUDE="gpu[01-04]", AFTER="77")
    assert r.returncode == 0, r.stderr
    lines = calls(tmp_path)
    assert all("--exclude=gpu[01-04]" in c for c in lines)
    assert dependency(job(lines, "pp-outlines-2025")) == ["77"]


@pytest.mark.parametrize(("start", "first", "count"), [
    ("simplify", "pp-simplify-s-2025", 5),
    ("merge", "pp-merge-2025", 2),
    ("fiboa", "pp-fiboa-2025", 1),
    ("qa", "pp-qa-2025", 1),
])  # fmt: skip
def test_from_starts_later(tmp_path, start, first, count):
    r = run_year(tmp_path, "15TVG_0_0\n", FROM=start, SCORES="", INDEX_DIR="")
    assert r.returncode == 0, r.stderr
    lines = calls(tmp_path)
    assert len(lines) == count and "-J pp-outlines" not in "".join(lines)
    assert dependency(job(lines, first)) == []
    if start == "qa":
        assert lines[0].split(" NSHARDS=")[0].endswith("qa_vector.sbatch")


def test_refuses_bad_input(tmp_path):
    assert run_year(tmp_path, "15TVG_0_0\n", FROM="pmtiles").returncode == 2
    assert run_year(tmp_path, "\n\n").returncode == 1
    assert run_year(tmp_path, "15TVG_0_0\n", FTW_LAND_POLYGONS="").returncode != 0
    assert run_year(tmp_path, "15TVG_0_0\n", INDEX_DIR="").returncode != 0
    assert calls(tmp_path) == []


def fiboa_job(tmp_path: Path, submit_out: str, rc: int = 0):
    py = executable(tmp_path / "py", f"#!/bin/bash\nprintf '{submit_out}'\nexit {rc}\n")
    env = stub_env(tmp_path) | {
        "YEAR": "2025",
        "OUT": str(tmp_path / "run"),
        "FTW_LAND_POLYGONS": "land.parquet",
        "REPO": str(HERE.parents[1]),
        "PY": str(py),
        "EXCLUDE": "gpu01",
    }
    return subprocess.run(
        ["bash", str(HERE / "fiboa.sbatch")], capture_output=True, text=True, check=False,
        env=env,
    )  # fmt: skip


def test_fiboa_job_runs_qa_after_every_zone(tmp_path):
    out = "15 120000 single 501\\n36 900000 K=2 502 503 504\\n"
    r = fiboa_job(tmp_path, out)
    assert r.returncode == 0, r.stderr
    (qa,) = calls(tmp_path)
    assert qa.split(" NSHARDS=")[0].endswith("qa_vector.sbatch")
    assert dependency(qa) == ["501", "504"]
    assert "--exclude=gpu01" in qa and "-J pp-qa-2025" in qa


@pytest.mark.parametrize(("out", "rc"), [("", 0), ("15 120000 single 501\\n", 1)])
def test_fiboa_job_fails_without_qa(tmp_path, out, rc):
    assert fiboa_job(tmp_path, out, rc).returncode != 0
    assert calls(tmp_path) == []


def test_size_classes_partition_the_tiles(tmp_path):
    sizes = {"a": 0, "b": 39_999_999, "c": 40_000_000, "d": 199_999_999, "e": 200_000_000}
    for t, n in sizes.items():
        with open(tmp_path / f"{t}.parquet", "wb") as f:
            f.truncate(n)
    tiles = [*sizes, "missing"]
    classes = [by_size(tiles, tmp_path, lo, hi) for lo, hi in ((0, 40), (40, 200), (200, math.inf))]
    assert classes == [["a", "b", "missing"], ["c", "d"], ["e"]]
