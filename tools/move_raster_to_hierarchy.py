#!/usr/bin/env python3
"""Move the raster COGs into per-item folders (user-approved 2026-10-01).

Executes the copy half of the approved move: server-side S3 copies from the
flat ``raster/{year}/{tile}.tif`` layout to the per-item hierarchy
``raster/{year}/{tile}/{tile}.tif`` — the layout the vector tree already
uses (one folder per item, data beside metadata, PORTO-CORE-071). The plan
is derived from ``index/raster.parquet``, never guessed from tile ids.
Deletion of the old keys happens separately, after the index and item
metadata flip, via ``--delete-old`` — and only with explicit user approval.

    python3 tools/move_raster_to_hierarchy.py              # dry run: counts only
    python3 tools/move_raster_to_hierarchy.py --confirm    # copy phase (idempotent)
    python3 tools/move_raster_to_hierarchy.py --delete-old # after approval

Every object is under the 5 GB CopyObject limit (max observed 1.83 GB), so
each move is one server-side call with no data egress. One paginated listing
of ``raster/`` up front replaces 67k per-object HEADs: a destination already
present with the right size is skipped, so re-runs sweep stragglers. boto3,
not the aws CLI — 67k subprocess startups would cost more than the copies.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import duckdb
from botocore.config import Config

BUCKET = "us-west-2.opendata.source.coop"
PREFIX = "ftw/global-data-beta"
INDEX = "https://data.source.coop/ftw/global-data-beta/index/raster.parquet"
PUBLIC_BASE = "https://data.source.coop/ftw/global-data-beta"
COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"


def plan() -> list[dict]:
    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-beta-catalog)"})
    con.execute("INSTALL httpfs; LOAD httpfs; SET http_retries=8;")
    rows = con.execute(
        f"SELECT year, tile_key, href, size_bytes FROM '{INDEX}'"
    ).fetchall()
    entries = []
    for year, tile, href, size in rows:
        if not href.startswith(PUBLIC_BASE + "/"):
            sys.exit(f"index href outside public base: {href}")
        old = href[len(PUBLIC_BASE) + 1:]
        new = f"raster/{year}/{tile}/{tile}.tif"
        if old == new:
            continue  # already moved and index already flipped
        entries.append({"old": old, "new": new, "size": size})
    return entries


def list_sizes(s3) -> dict[str, int]:
    sizes = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{PREFIX}/raster/"):
        for obj in page.get("Contents", []):
            sizes[obj["Key"][len(PREFIX) + 1:]] = obj["Size"]
    return sizes


def copy_one(s3, entry: dict) -> str | None:
    try:
        s3.copy_object(
            Bucket=BUCKET, Key=f"{PREFIX}/{entry['new']}",
            CopySource={"Bucket": BUCKET, "Key": f"{PREFIX}/{entry['old']}"},
            MetadataDirective="REPLACE", ContentType=COG_TYPE,
        )
        head = s3.head_object(Bucket=BUCKET, Key=f"{PREFIX}/{entry['new']}")
        if head["ContentLength"] != entry["size"]:
            return f"{entry['new']}: size mismatch after copy"
        return None
    except Exception as exc:  # noqa: BLE001 — collected and retried by re-run
        return f"{entry['old']}: {exc}"[:200]


def delete_one(s3, entry: dict, sizes: dict[str, int]) -> str | None:
    if sizes.get(entry["new"]) != entry["size"]:
        return f"REFUSED: {entry['new']} absent/mismatched; keeping old"
    try:
        s3.delete_object(Bucket=BUCKET, Key=f"{PREFIX}/{entry['old']}")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{entry['old']}: {exc}"[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="actually copy; without it, report counts only")
    parser.add_argument("--delete-old", action="store_true",
                        help="delete verified-copied old keys (separate "
                             "user approval required)")
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-west-2",
                      config=Config(max_pool_connections=max(args.workers, 10),
                                    retries={"max_attempts": 8,
                                             "mode": "adaptive"}))
    entries = plan()
    print(f"plan: {len(entries):,} object(s) from index", flush=True)
    sizes = list_sizes(s3)
    print(f"bucket: {len(sizes):,} object(s) under raster/", flush=True)

    if args.delete_old:
        todo = [e for e in entries if e["old"] in sizes]
        action = lambda e: delete_one(s3, e, sizes)  # noqa: E731
        verb = "deleted (old flat layout)"
    else:
        todo = [e for e in entries if sizes.get(e["new"]) != e["size"]]
        action = lambda e: copy_one(s3, e)  # noqa: E731
        verb = "copied to per-item folders"
    print(f"todo: {len(todo):,} ({len(entries) - len(todo):,} already done)",
          flush=True)
    if not args.confirm and not args.delete_old:
        print("dry run; pass --confirm to copy")
        return 0
    if not todo:
        print("nothing to do")
        return 0

    failures, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(action, e): e for e in todo}
        for future in as_completed(futures):
            done += 1
            err = future.result()
            if err:
                failures.append(err)
                print(f"  FAIL {err}", file=sys.stderr, flush=True)
            if done % 500 == 0 or done == len(todo):
                rate = done / max(time.time() - t0, 1)
                print(f"  {done:,}/{len(todo):,} "
                      f"({rate:,.0f}/s, {len(failures)} failed)", flush=True)
    if failures:
        print(f"{len(failures)} failure(s); re-run to retry", file=sys.stderr)
        return 1
    print(f"OK: {len(todo):,} object(s) {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
