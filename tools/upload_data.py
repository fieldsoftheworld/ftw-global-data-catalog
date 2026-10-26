#!/usr/bin/env python3
"""Upload staged data files to the same bucket prefix the catalog publishes to.

``tools/publish.py`` walks ``publish_dir`` and nothing else. That boundary is
the publish contract and this script does not widen it. GeoParquet files and
PMTiles archives are too large for git, so they live outside ``catalog/`` and
never reach the bucket through the publisher. This script carries them there.

Every rule the two scripts share comes from ``publish.py``. This file imports
the sentinel guard, the content types, the change detection, the non-recursive
listing, and the upload pool. It adds one thing, a second walk root.

    python3 tools/upload_data.py            # dry run: what would change
    python3 tools/upload_data.py --confirm  # upload; needs AWS credentials
    python3 tools/upload_data.py --confirm --force   # re-upload everything

``data_dir`` in ``catalog.publish.yaml`` names the staging directory that
holds the data (``staging-data/``, gitignored). Two gates decide what
uploads. The path gate admits only files under ``data_dir``. The extension
gate admits only the suffixes in PUBLISHABLE_SUFFIXES. Both apply. It never
deletes, exactly as ``publish.py`` never deletes.

A multipart ETag is the MD5 of the part MD5s plus ``-N``. Change detection
compares a multipart object on size alone unless ``--hash`` is given, because
re-hashing every multi-gigabyte archive on each dry run is slow. After an
upload every uploaded object is checked against the local file by size and
ETag, multipart included, and the run exits 1 on a mismatch.

    python3 tools/upload_data.py --verify-only            # check the whole staging tree
    python3 tools/upload_data.py --verify-only --public-check 50   # and 50 public reads
"""
from __future__ import annotations

import argparse
import hashlib
import random
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish import (  # noqa: E402
    ROOT,
    Upload,
    aws_cli,
    content_type_for,
    is_publishable,
    is_unchanged,
    load_config,
    md5,
    remote_index,
    split_s3_uri,
    unedited_sentinels,
    upload_all,
)


MIB = 1024 * 1024
# aws s3 cp splits at multipart_chunksize: 8 MiB by default, 16 MiB where a
# profile sets it (r1's uploads used 16 MiB)
PART_SIZES = (8 * MIB, 16 * MIB)
HASH_WORKERS = 8
EDGE = 64 * 1024
USER_AGENT = "ftw-global-data-catalog upload check"


def part_size(size: int, parts: int) -> int:
    """The part size of a ``parts``-part upload of ``size`` bytes.

    One of PART_SIZES when it fits; otherwise the whole MiB the CLI grows its
    chunk to when a file would need more than 10,000 parts.
    """
    for p in PART_SIZES:
        if (parts - 1) * p < size <= parts * p:
            return p
    p = -(-size // parts)
    return -(-p // MIB) * MIB


def expected_etag(path: Path, remote_etag: str) -> str:
    """The ETag S3 gives ``path`` when it is uploaded the way ``remote_etag`` was."""
    tag = remote_etag.strip('"')
    if "-" not in tag:
        return md5(path)
    parts = int(tag.rsplit("-", 1)[1])
    size = part_size(path.stat().st_size, parts)
    digests = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(size), b""):
            digests += hashlib.md5(chunk).digest()  # noqa: S324 - S3 ETag
    return f"{hashlib.md5(digests).hexdigest()}-{parts}"  # noqa: S324


def mismatches(
    uploads: list[Upload], index: dict[str, tuple[int, str]],
) -> dict[str, list[str]]:
    """Keys missing from ``index``, of another size, or with another ETag."""
    missing = [u.key for u in uploads if u.key not in index]
    present = [u for u in uploads if u.key in index]
    size = [u.key for u in present if index[u.key][0] != u.local.stat().st_size]
    todo = [u for u in present if u.key not in size]
    with ThreadPoolExecutor(HASH_WORKERS) as pool:
        tags = list(pool.map(lambda u: expected_etag(u.local, index[u.key][1]), todo))
    etag = [u.key for u, t in zip(todo, tags) if t != index[u.key][1].strip('"')]
    return {"missing": missing, "size": size, "etag": etag}


def public_check(
    uploads: list[Upload], public_base: str, prefix: str, n: int, seed: int = 0,
) -> list[str]:
    """Read the first and last 64 KiB of ``n`` objects from the public URL.

    Each must equal the local bytes, and the total in Content-Range must equal
    the local size. Returns the keys that fail.
    """
    sample = random.Random(seed).sample(uploads, min(n, len(uploads)))
    failed = []
    for u in sample:
        url = f"{public_base.rstrip('/')}/{u.key[len(prefix):].lstrip('/')}"
        size = u.local.stat().st_size
        with u.local.open("rb") as handle:
            head = handle.read(EDGE)
            handle.seek(max(0, size - EDGE))
            tail = handle.read()
        ok = True
        for start, want in ((0, head), (max(0, size - EDGE), tail)):
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Range": f"bytes={start}-{start + len(want) - 1}",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    got = resp.read()
                    total = resp.headers.get("Content-Range", "").rsplit("/", 1)[-1]
            except OSError as exc:
                print(f"  public read failed: {url}: {exc}", file=sys.stderr)
                ok = False
                break
            if got != want or total != str(size):
                ok = False
        if not ok:
            failed.append(u.key)
    return failed


def report(found: dict[str, list[str]]) -> bool:
    """Print the mismatches; True when there are none."""
    bad = [k for keys in found.values() for k in keys]
    for kind, keys in found.items():
        if keys:
            print(f"  {kind}: {len(keys)}", file=sys.stderr)
    for key in bad[:20]:
        print(f"    {key}", file=sys.stderr)
    return not bad


def remote_data_index(
    uploads: list[Upload], config: dict[str, str],
) -> dict[str, tuple[int, str]]:
    """Size and ETag for the staged keys' prefixes, listed recursively.

    publish.py lists per directory because catalog/ directories share the
    bucket prefix with the data tree. The data tree is the opposite shape:
    tens of thousands of per-item directories under a handful of top-level
    prefixes (raster/, vector/, index/), where one recursive paginated
    listing per prefix costs ~1 request per 1,000 objects and a per-directory
    walk costs one subprocess per directory. Falls back to the per-directory
    walk when boto3 is unavailable, and to {} (everything looks new) when a
    listing fails — same erring-toward-upload contract as remote_index.
    """
    try:
        import boto3
    except ImportError:
        return remote_index(uploads, config)
    bucket, prefix = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    # Upload.key is the full object key; the top-level data prefix is the
    # first segment after the write prefix (raster/, vector/, index/).
    tops = {u.key[len(prefix) + 1:].split("/", 1)[0] for u in uploads}
    index: dict[str, tuple[int, str]] = {}
    s3 = boto3.client("s3", region_name=region)
    try:
        for top in sorted(tops):
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=bucket, Prefix=f"{prefix}/{top}/"
            ):
                for obj in page.get("Contents", []):
                    # Full keys, prefix included — Upload.key is the full key.
                    index[obj["Key"]] = (obj["Size"], obj["ETag"].strip('"'))
    except Exception as exc:  # noqa: BLE001 — same contract as remote_index
        print(f"note: could not list s3://{bucket}/{prefix} ({exc}); "
              "treating every file as changed")
        return {}
    return index

# The suffixes that may reach the bucket. This is an allow-list, and it names
# what may pass rather than what may not. A staging tree grows new scratch
# files over time. An allow-list stays correct when it does, and a deny-list
# does not.
PUBLISHABLE_SUFFIXES = {
    ".parquet",
    ".pmtiles",
    ".tif",
    ".tiff",
    # Phase 4 browse artifacts: per-item thumbnails and overview thumbnails.
    ".png",
    ".webp",
}


def data_root(config: dict[str, str], root: Path = ROOT) -> Path:
    """The staging directory this script walks.

    Reads the ``data_dir`` key. A relative value resolves against the
    repository root. Exits with a message when the key is absent or when the
    directory does not exist.
    """
    configured = config.get("data_dir", "")
    if not configured:
        sys.exit(
            "catalog.publish.yaml sets no data_dir, so there is nothing to "
            "upload.\nSet data_dir to the directory that holds your data "
            "files."
        )
    base = Path(configured)
    if not base.is_absolute():
        base = root / base
    base = base.resolve()
    if not base.is_dir():
        sys.exit(f"data_dir does not exist: {base}")
    return base


def is_data_publishable(rel: Path) -> bool:
    """True for a staged file that both gates admit.

    The path gate runs in ``collect_data_uploads``. This is the second gate.
    It applies the dotfile rule of ``publish.py`` and then the suffix
    allow-list.
    """
    return is_publishable(rel) and rel.suffix.lower() in PUBLISHABLE_SUFFIXES


def collect_data_uploads(
    config: dict[str, str], root: Path = ROOT
) -> list[Upload]:
    """Every staged data file that would be uploaded, in sorted order.

    The walk is rooted at ``data_dir`` and nothing else. Keys go under the
    same ``write_prefix`` the catalog publishes to, so the data sits beside
    the metadata that describes it: stage a file at
    ``staging-data/vector/fields-yearly/fields-2025.pmtiles`` and it lands at
    ``vector/fields-yearly/fields-2025.pmtiles`` in the bucket.
    """
    _, prefix = split_s3_uri(config["write_prefix"])
    base = data_root(config, root)
    uploads = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if not is_data_publishable(rel):
            continue
        key = f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
        uploads.append(Upload(path, key, content_type_for(path)))
    return uploads


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload staged data files to the catalog's bucket prefix.",
        epilog="Dry run by default. Never deletes.",
    )
    parser.add_argument("--confirm", action="store_true",
                        help="actually upload")
    parser.add_argument(
        "--force", action="store_true",
        help="re-upload everything; skip the remote listing",
    )
    parser.add_argument(
        "--retries", type=int, default=4,
        help="retries per object on a transient S3 failure (default: 4)",
    )
    parser.add_argument(
        "--hash", action="store_true",
        help="compare multipart objects by ETag too, not size alone (reads every file)",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="upload nothing; check every staged file against the bucket by size and ETag",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="skip the size and ETag check of the uploaded objects",
    )
    parser.add_argument(
        "--public-check", type=int, default=0, metavar="N",
        help="also read the first and last 64 KiB of N files from public_base",
    )
    args = parser.parse_args()

    config = load_config()
    base = data_root(config)

    stale = unedited_sentinels(config)
    if stale:
        print("catalog.publish.yaml still carries template values:")
        for value in stale:
            print(f"  {value}")
        return 1

    bucket, prefix = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    uploads = collect_data_uploads(config)
    if not uploads:
        print(f"nothing under {base}/ to upload", file=sys.stderr)
        return 1

    if args.verify_only:
        index = remote_data_index(uploads, config)
        if not index:
            sys.exit("remote listing failed or empty; nothing verified")
        print(f"verifying {len(uploads)} file(s) against s3://{bucket}/{prefix}")
        ok = report(mismatches(uploads, index))
        if args.public_check:
            failed = public_check(uploads, config["public_base"], prefix, args.public_check)
            print(f"public reads: {min(args.public_check, len(uploads)) - len(failed)} "
                  f"of {min(args.public_check, len(uploads))} match")
            ok &= not failed
        print("all match" if ok else "MISMATCH: re-upload the files above with --force")
        return 0 if ok else 1

    index = {} if args.force else remote_data_index(uploads, config)
    if not index and not args.force and args.confirm:
        # Err-toward-upload is right for a dry run; on --confirm it just
        # re-uploaded ~30 GB of unchanged data after one failed listing
        # (2026-10-02). A confirm with no listing is a blind upload: refuse,
        # and let the operator retry or say --force to mean it.
        sys.exit("remote listing failed or empty; refusing blind --confirm "
                 "(retry, or pass --force to upload everything)")
    changed = [
        u for u in uploads
        if args.force
        or not is_unchanged(u, index, multipart_matches_on_size=True)
    ]
    if args.hash and not args.force:
        changing = {u.key for u in changed}
        kept = [u for u in uploads if u.key not in changing]
        stale = {k for keys in mismatches(kept, index).values() for k in keys}
        changed += [u for u in kept if u.key in stale]

    print(f"data_dir:    {base}/")
    print(f"target:      s3://{bucket}/{prefix}")
    print(f"suffixes:    {', '.join(sorted(PUBLISHABLE_SUFFIXES))}")
    print(f"{len(uploads)} file(s) staged, {len(changed)} to upload")
    print("this never deletes; removing a file here does not unpublish it")

    if not args.confirm:
        for upload in changed[:20]:
            print(f"  would upload  {upload.key}  ({upload.content_type})")
        if len(changed) > 20:
            print(f"  ... and {len(changed) - 20} more")
        print("\ndry run. re-run with --confirm to upload.")
        return 0

    if not changed:
        print("nothing to upload")
        return 0

    aws = aws_cli()
    if aws is None:
        sys.exit("aws CLI is required to upload and was not found on PATH")

    failed = upload_all(changed, bucket, region, aws, args.retries)
    if failed:
        print(f"\n{len(failed)} of {len(changed)} file(s) failed:",
              file=sys.stderr)
        for key in sorted(failed):
            print(f"  {key}", file=sys.stderr)
        return 1
    print(f"\nuploaded {len(changed)} file(s)")
    if args.no_verify:
        return 0
    after = remote_data_index(changed, config)
    if not after:
        print("could not list the bucket to verify the upload", file=sys.stderr)
        return 1
    if not report(mismatches(changed, after)):
        print("MISMATCH after upload: re-run with --force for the files above",
              file=sys.stderr)
        return 1
    print(f"verified {len(changed)} file(s) by size and ETag")
    if args.public_check:
        failed = public_check(changed, config["public_base"], prefix, args.public_check)
        if failed:
            print(f"public reads differ for {len(failed)} file(s)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
