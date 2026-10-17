"""run_year.sh submits the PMTiles chain; sbatch is a stub that records the calls."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

STUB = """#!/bin/bash
n=$(cat "$STUB_DIR/count" 2>/dev/null || echo 100); echo $((n + 1)) > "$STUB_DIR/count"
echo "$* MODE=${MODE:-} IDX=${IDX:-}" >> "$STUB_DIR/calls.log"
echo $((n + 1))
"""


def submit(tmp_path: Path, year: str = "2019", **env: str) -> tuple[str, list[str]]:
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "sbatch").write_text(STUB)
    (stub / "sbatch").chmod(stub.joinpath("sbatch").stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(HERE / "run_year.sh", work / "run_year.sh")
    base = {"PATH": f"{stub}:{os.environ['PATH']}", "STUB_DIR": str(tmp_path)}
    out = subprocess.run(
        ["bash", str(work / "run_year.sh"), year],
        capture_output=True, text=True, check=True, env={**base, **env},
    ).stdout  # fmt: skip
    return out.strip(), (tmp_path / "calls.log").read_text().splitlines()


def find(calls: list[str], job: str) -> str:
    (line,) = [c for c in calls if f"-J {job} " in c or c.endswith(f"-J {job}")]
    return line


def test_default_is_four_big_shards_and_no_check(tmp_path):
    out, calls = submit(tmp_path)
    shards = [c for c in calls if "MODE=shard" in c]
    assert len(shards) == 4
    assert all("-c32 --mem=160G" in c for c in shards)
    assert all("--exclude=rails[04-15]" in c for c in calls)
    assert "check none" in out and not any("pmcheck" in c for c in calls)
    upload = find(calls, "pm-upload-2019")
    assert re.search(r"--dependency=afterok:(\d+) upload_year.sbatch", upload)
    merge_id = re.search(r"merge (\d+)", out).group(1)
    assert f"afterok:{merge_id} upload_year" in upload


def test_eight_shards_are_smaller(tmp_path):
    _, calls = submit(tmp_path, N="8")
    shards = [c for c in calls if "MODE=shard" in c]
    assert len(shards) == 8
    assert all("-c16 --mem=70G" in c for c in shards)
    assert [re.search(r"IDX=(\d+)", c).group(1) for c in shards] == [str(i) for i in range(8)]
    merge = find(calls, "pm-merge-2019")
    assert merge.count(":") >= 8, "the merge waits for every shard"


def test_the_exclusion_can_be_lifted_and_sizes_overridden(tmp_path):
    _, calls = submit(tmp_path, EXCLUDE="", SHARD_C="8", SHARD_M="40G")
    assert not any("--exclude" in c for c in calls)
    assert all("-c8 --mem=40G" in c for c in calls if "MODE=shard" in c)


def test_check_gates_the_upload_and_upload_can_be_held(tmp_path):
    out, calls = submit(tmp_path, SRC_ROOT="/zones", REF_REPORT="/ref.json", PY="/venv/bin/python")
    check = find(calls, "pm-check-2019")
    assert "/venv/bin/python" in check and "merge-report-2019.json /ref.json" in check
    assert "/zones" in check
    check_id = re.search(r"check (\d+)", out).group(1)
    assert f"afterok:{check_id} upload_year" in find(calls, "pm-upload-2019")


def test_upload_can_be_held(tmp_path):
    out, calls = submit(tmp_path, SRC_ROOT="/zones", UPLOAD="0")
    assert "upload held" in out
    assert not any("upload_year" in c for c in calls)
