#!/usr/bin/env python3
"""Move the zone parquets into hive partition folders (user-approved).

Executes the copy half of the approved move: server-side S3 copies from the
flat ``vector/{year}/utm{NN}.parquet`` layout to the canonical hive layout
``vector/{year}/zone=NN/utm{NN}.parquet``, from the plan written by the
index scan (``staging-data/checksums/move_plan.json``). Deletion of the old
keys happens separately, after the metadata flip, via ``--delete-old``.

    python3 tools/move_to_hive.py            # copy phase (idempotent)
    python3 tools/move_to_hive.py --delete-old

Copies are verified by size against the plan; an object already present at
the destination with the right size is skipped, so re-runs sweep stragglers.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "staging-data" / "checksums" / "move_plan.json"
AWS = "/u/cholmes/micromamba/envs/ftw/bin/aws"
BUCKET = "us-west-2.opendata.source.coop"
PREFIX = "ftw/global-data-2e"


def head(key: str) -> int | None:
    proc = subprocess.run(
        [AWS, "s3api", "head-object", "--bucket", BUCKET,
         "--key", f"{PREFIX}/{key}", "--region", "us-west-2",
         "--output", "json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)["ContentLength"]


def copy(entry: dict) -> str | None:
    """Copy one object; returns an error string or None."""
    if head(entry["new"]) == entry["size"]:
        return None
    proc = subprocess.run(
        [AWS, "s3", "cp",
         f"s3://{BUCKET}/{PREFIX}/{entry['old']}",
         f"s3://{BUCKET}/{PREFIX}/{entry['new']}",
         "--region", "us-west-2", "--copy-props", "none",
         "--content-type", "application/vnd.apache.parquet"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return f"{entry['old']}: {(proc.stderr or '').strip()[:160]}"
    if head(entry["new"]) != entry["size"]:
        return f"{entry['new']}: size mismatch after copy"
    return None


def delete_old(entry: dict) -> str | None:
    if head(entry["new"]) != entry["size"]:
        return f"REFUSED: {entry['new']} absent/mismatched; keeping old"
    proc = subprocess.run(
        [AWS, "s3", "rm", f"s3://{BUCKET}/{PREFIX}/{entry['old']}",
         "--region", "us-west-2"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return f"{entry['old']}: {(proc.stderr or '').strip()[:160]}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-old", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    plan = json.loads(PLAN.read_text())
    action = delete_old if args.delete_old else copy
    failures = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(action, e): e for e in plan}
        for future in as_completed(futures):
            done += 1
            err = future.result()
            if err:
                failures.append(err)
                print(f"  FAIL {err}", file=sys.stderr, flush=True)
            if done % 20 == 0 or done == len(plan):
                print(f"  {done}/{len(plan)}", flush=True)
    if failures:
        print(f"{len(failures)} failure(s); re-run to retry", file=sys.stderr)
        return 1
    print(f"OK: {len(plan)} object(s) "
          + ("deleted (old layout)" if args.delete_old else "copied to hive"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
