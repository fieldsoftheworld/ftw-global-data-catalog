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

**Change detection is weaker here than it is for the catalog.** A multipart
ETag is not an MD5, so a multipart object compares on size alone — re-hashing
a multi-gigabyte PMTiles archive on every run is not viable. A truncated
object of the correct size stays accepted on every later run. Use ``--force``
after you replace a file with one of the same size.
"""
from __future__ import annotations

import argparse
import sys
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
    remote_index,
    split_s3_uri,
    unedited_sentinels,
    upload_all,
)


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

    index = {} if args.force else remote_data_index(uploads, config)
    changed = [
        u for u in uploads
        if args.force
        or not is_unchanged(u, index, multipart_matches_on_size=True)
    ]

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
