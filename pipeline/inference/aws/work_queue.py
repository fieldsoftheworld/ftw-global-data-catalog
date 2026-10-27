#!/usr/bin/env python3
"""S3 work queue for GPU inference on EC2 (``launch.sh``, ``bootstrap.sh``).

A unit is a text file of "YEAR TILE" lines under {prefix}units/. Units are ``{year}_{shard:03d}``:
shard ``i`` of ``n`` of ``stream.work_list(year)``, the tiles ``stream.py --shard i --num-shards n``
would run, so a cluster and AWS can split one year by shard. Layout under s3://BUCKET/PREFIX:

    units/{unit}.txt          tile list
    claims/{unit}.{attempt}   conditional put (If-None-Match); body = owner id or "rails"
    hb/{instance}             heartbeat every 30 s; hb/{instance}.dead on exit or spot notice
    done/{unit}               JSON {rc, tiles, owner, ...}
    raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif   the score COG, at the bucket's own key
    _qa/{year}/{tile}.json    sidecar (uploaded first)

The outline fleet (``pipeline/postprocessing/aws``) uses the same queue with ``--outputs
outlines``: a tile is finished once ``outlines/{year}/{tile}.parquet`` exists.

A claim whose owner stopped heart-beating (STALE_S) or wrote hb/{id}.dead is taken over by
claiming the next attempt number; tiles that already have a COG are skipped (``todo``). Nothing is
ever deleted. Rails-reserved claims are never taken over.

    python work_queue.py units --years 2025 --nshards 256 --index-dir IDX --out /tmp/units
    python work_queue.py claim --bucket B --prefix infer/ --owner i-0abc [--years 2025]
    python work_queue.py status --bucket B --prefix infer/
    python work_queue.py requeue --bucket B --prefix infer/   # units that finished with gaps
"""

import argparse
import json
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STALE_S = 600


def _s3() -> Any:
    import boto3

    return boto3.client("s3")


def _list(s3: Any, bucket: str, prefix: str) -> dict[str, datetime]:
    """key (prefix stripped) -> LastModified for every object under prefix."""
    out: dict[str, datetime] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            out[o["Key"][len(prefix) :]] = o["LastModified"]
    return out


def _get(s3: Any, bucket: str, key: str) -> str:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode().strip()


def _put_new(s3: Any, bucket: str, key: str, body: str) -> bool:
    """Create key only if absent (S3 conditional write); False if someone else holds it."""
    from botocore.exceptions import ClientError

    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode(), IfNoneMatch="*")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict"):
            return False
        raise
    return True


def _claims(s3: Any, a: argparse.Namespace) -> dict[str, list[int]]:
    by_unit: dict[str, list[int]] = {}
    for k in _list(s3, a.bucket, a.prefix + "claims/"):
        unit, att = k.rsplit(".", 1)
        by_unit.setdefault(unit, []).append(int(att))
    return by_unit


def _alive(hb: dict[str, datetime], owner: str) -> bool:
    if owner.startswith("rails"):
        return True
    t = hb.get(owner)
    if t is None or f"{owner}.dead" in hb:
        return False
    return (datetime.now(UTC) - t).total_seconds() < STALE_S


def cmd_units(a: argparse.Namespace) -> None:
    """Write unit files from the work list ``stream.py`` uses."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stream import work_list

    a.out.mkdir(parents=True, exist_ok=True)
    for y in a.years:
        jobs = work_list(a.index_dir / f"tile_index_{y}.parquet", a.keep_list)
        for i in range(a.nshards):
            tiles = [j[0] for j in jobs[i :: a.nshards]]
            (a.out / f"{y}_{i:03d}.txt").write_text("".join(f"{y} {t}\n" for t in tiles))
        print(f"{y}: {len(jobs)} tiles in {a.nshards} units")


def cmd_claim(a: argparse.Namespace) -> None:
    s3 = _s3()
    units = sorted(k[:-4] for k in _list(s3, a.bucket, a.prefix + "units/") if k.endswith(".txt"))
    if a.years:
        units = [u for u in units if u.split("_")[0] in set(map(str, a.years))]
    done = set(_list(s3, a.bucket, a.prefix + "done/"))
    claims = _claims(s3, a)
    hb = _list(s3, a.bucket, a.prefix + "hb/")
    # start at an owner-specific offset so boxes starting together do not race for one unit
    off = zlib.crc32(a.owner.encode()) % max(len(units), 1)
    for u in units[off:] + units[:off]:
        if u in done:
            continue
        att = claims.get(u, [])
        nxt = 0
        if att:
            last = max(att)
            owner = _get(s3, a.bucket, f"{a.prefix}claims/{u}.{last}")
            if owner == a.owner or _alive(hb, owner):
                continue
            nxt = last + 1
        if _put_new(s3, a.bucket, f"{a.prefix}claims/{u}.{nxt}", a.owner):
            print(u)
            return
    sys.exit(1)  # nothing left to claim


def cmd_reserve(a: argparse.Namespace) -> None:
    """Mark units as Rails' (claims/{unit}.0 = "rails"); AWS boxes then never take them."""
    s3 = _s3()
    for u in a.units:
        ok = _put_new(s3, a.bucket, f"{a.prefix}claims/{u}.0", "rails")
        print(f"{u}: {'reserved' if ok else 'ALREADY CLAIMED (check status)'}")


def _missing(s3: Any, a: argparse.Namespace) -> list[tuple[str, str]]:
    lines = _get(s3, a.bucket, f"{a.prefix}units/{a.unit}.txt").splitlines()
    have = {y: _finished(s3, a, y) for y in sorted({ln.split()[0] for ln in lines})}
    return [(y, t) for y, t in (ln.split() for ln in lines) if t not in have[y]]


def _cogs(s3: Any, a: argparse.Namespace, year: str) -> set[str]:
    """Tile keys with a COG under raster/{year}/zone=*/gzd=*/{tile}/{tile}.tif."""
    keys = _list(s3, a.bucket, f"{a.prefix}raster/{year}/")
    return {k.rsplit("/", 1)[1][:-4] for k in keys if k.endswith(".tif") and k.count("/") == 3}


def _outlines(s3: Any, a: argparse.Namespace, year: str) -> set[str]:
    """Tile keys with an outline file at outlines/{year}/{tile}.parquet."""
    keys = _list(s3, a.bucket, f"{a.prefix}outlines/{year}/")
    return {k[: -len(".parquet")] for k in keys if k.endswith(".parquet") and "/" not in k}


def _finished(s3: Any, a: argparse.Namespace, year: str) -> set[str]:
    return (_outlines if a.outputs == "outlines" else _cogs)(s3, a, year)


def cmd_todo(a: argparse.Namespace) -> None:
    for y, t in _missing(_s3(), a):
        print(y, t)


def cmd_done(a: argparse.Namespace) -> None:
    body = {"rc": a.rc, "owner": a.owner, "tiles": a.tiles, "failed": a.failed, "t": time.time()}
    _s3().put_object(
        Bucket=a.bucket, Key=f"{a.prefix}done/{a.unit}", Body=json.dumps(body).encode()
    )


def cmd_requeue(a: argparse.Namespace) -> None:
    """New unit {unit}r1 holding the tiles a finished unit left without a COG (rc != 0)."""
    s3 = _s3()
    units = set(_list(s3, a.bucket, a.prefix + "units/"))
    for u in _list(s3, a.bucket, a.prefix + "done/"):
        if json.loads(_get(s3, a.bucket, f"{a.prefix}done/{u}"))["rc"] == 0:
            continue
        if f"{u}r1.txt" in units:  # already requeued; a failed {u}r1 is requeued as {u}r1r1
            continue
        a.unit = u
        lines = _missing(s3, a)
        if lines and not a.dry_run:
            body = "".join(f"{y} {t}\n" for y, t in lines).encode()
            s3.put_object(Bucket=a.bucket, Key=f"{a.prefix}units/{u}r1.txt", Body=body)
        print(f"{u}: {len(lines)} tiles missing{'' if not lines else f' -> {u}r1'}")


def cmd_status(a: argparse.Namespace) -> None:
    s3 = _s3()
    units = [k[:-4] for k in _list(s3, a.bucket, a.prefix + "units/") if k.endswith(".txt")]
    done = set(_list(s3, a.bucket, a.prefix + "done/"))
    claims = _claims(s3, a)
    hb = _list(s3, a.bucket, a.prefix + "hb/")
    live, stale, rails = 0, [], 0
    for u, att in claims.items():
        if u in done:
            continue
        owner = _get(s3, a.bucket, f"{a.prefix}claims/{u}.{max(att)}")
        if owner.startswith("rails"):
            rails += 1
        elif _alive(hb, owner):
            live += 1
        else:
            stale.append(u)
    bad = [u for u in done if json.loads(_get(s3, a.bucket, f"{a.prefix}done/{u}"))["rc"] != 0]
    print(
        f"units {len(units)}: done {len(done)} (rc!=0: {len(bad)} {bad[:5]}), running {live}, "
        f"rails {rails}, stale {len(stale)} {stale[:5]}, "
        f"unclaimed {len(set(units) - set(claims) - done)}"
    )
    now = datetime.now(UTC)
    boxes = [k for k in hb if not k.endswith(".dead") and f"{k}.dead" not in hb]
    print(f"boxes heart-beating: {sum((now - hb[k]).total_seconds() < STALE_S for k in boxes)}")
    years = sorted({u.split("_")[0] for u in units})
    for y in years:
        what = "COGs" if a.outputs == "raster" else "outline files"
        print(f"{a.outputs}/{y}: {len(_finished(s3, a, y))} {what}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("units")
    p.add_argument("--years", type=int, nargs="+", required=True)
    p.add_argument("--nshards", type=int, default=256)
    p.add_argument("--index-dir", type=Path, required=True)
    p.add_argument("--keep-list", type=Path,
                   default=Path(__file__).resolve().parents[2] / "mosaics" / "keeplist.txt")
    p.add_argument("--out", type=Path, required=True)
    for name in ("claim", "reserve", "todo", "done", "requeue", "status"):
        p = sub.add_parser(name)
        p.add_argument("--bucket", required=True)
        p.add_argument("--prefix", required=True, help="e.g. infer/ (trailing slash)")
        if name in ("claim", "done"):
            p.add_argument("--owner", required=True)
        if name == "claim":
            p.add_argument("--years", type=int, nargs="*")
        if name == "requeue":
            p.add_argument("--dry-run", action="store_true")
        if name == "reserve":
            p.add_argument("units", nargs="+")
        if name in ("todo", "done"):
            p.add_argument("--unit", required=True)
        if name in ("todo", "requeue", "status"):
            p.add_argument("--outputs", choices=("raster", "outlines"), default="raster",
                           help="what a finished tile leaves: a score COG or an outline file")
        if name == "done":
            p.add_argument("--rc", type=int, required=True)
            p.add_argument("--tiles", type=int, default=0)
            p.add_argument("--failed", type=int, default=0)
    a = ap.parse_args()
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
