#!/usr/bin/env python3
"""Move the raster data into the unified grouped hierarchy (user-approved).

Phase 2 of the raster relayout (2026-10-01). Phase 1 (this morning, in git
history) copied the flat ``raster/{year}/{tile}.tif`` into per-item folders.
Chris then chose the unified grouped hierarchy, so this copies each tile's
COG **and** its ``{tile}.thumb.png`` from ``raster/{year}/{tile}/`` into

    raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/

where ``{ZZ}`` is the tile key's leading two digits and ``{GZD}`` those
digits plus the band letter (``01KFS_0_0`` → ``zone=01/gzd=01K``) — the same
derivation pinned in ``tools/build_raster_items.py``. The plan comes from
``index/raster.parquet`` (tifs) plus one recursive listing (thumb sizes),
never from guessed keys. Deletion of superseded keys happens separately,
after the index and metadata flip, via ``--delete-old`` — and only with
explicit user approval.

    python3 tools/move_raster_to_hierarchy.py              # dry run: counts only
    python3 tools/move_raster_to_hierarchy.py --confirm    # copy phase (idempotent)
    python3 tools/move_raster_to_hierarchy.py --delete-old # after approval

Every object is under the 5 GB CopyObject limit (max observed 1.83 GB), so
each move is one server-side call with no data egress. One paginated listing
of ``raster/`` up front replaces per-object HEADs: a destination already
present with the right size is skipped, so re-runs sweep stragglers. boto3,
not the aws CLI — 134k subprocess startups would cost more than the copies.
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
PNG_TYPE = "image/png"


def group(tile: str) -> tuple[str, str]:
    """(zone, gzd) from a tile key — pinned convention, asserted not assumed."""
    if not (tile[:2].isdigit() and tile[2].isalpha()):
        sys.exit(f"tile key {tile!r} does not start with NN + band letter")
    return tile[:2], tile[:3]


def plan(sizes: dict[str, int]) -> list[dict]:
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
        zone, gzd = group(tile)
        dest_dir = f"raster/{year}/zone={zone}/gzd={gzd}/{tile}"
        new = f"{dest_dir}/{tile}.tif"
        if old != new:
            entries.append({"old": old, "new": new, "size": size,
                            "type": COG_TYPE})
        # The thumbnail rides along from the same folder; its size comes from
        # the listing because the index does not carry it.
        old_thumb = f"{old.rsplit('/', 1)[0]}/{tile}.thumb.png"
        new_thumb = f"{dest_dir}/{tile}.thumb.png"
        if old_thumb != new_thumb and old_thumb in sizes:
            entries.append({"old": old_thumb, "new": new_thumb,
                            "size": sizes[old_thumb], "type": PNG_TYPE})
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
            MetadataDirective="REPLACE", ContentType=entry["type"],
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
    parser.add_argument("--workers", type=int, default=48)
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-west-2",
                      config=Config(max_pool_connections=max(args.workers, 10),
                                    retries={"max_attempts": 8,
                                             "mode": "adaptive"}))
    sizes = list_sizes(s3)
    print(f"bucket: {len(sizes):,} object(s) under raster/", flush=True)
    entries = plan(sizes)
    print(f"plan: {len(entries):,} object(s) to move", flush=True)

    if args.delete_old:
        todo = [e for e in entries if e["old"] in sizes]
        action = lambda e: delete_one(s3, e, sizes)  # noqa: E731
        verb = "deleted (superseded layout)"
    else:
        todo = [e for e in entries if sizes.get(e["new"]) != e["size"]]
        action = lambda e: copy_one(s3, e)  # noqa: E731
        verb = "copied to the grouped hierarchy"
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
