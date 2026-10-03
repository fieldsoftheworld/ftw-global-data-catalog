#!/usr/bin/env python3
"""Sync the published directory to object storage, 1:1.

``catalog/`` *is* the published catalog: everything under it is published, and
nothing outside it ever is. That boundary is the whole publish contract, and it
is enforced structurally — this script walks ``publish_dir`` and has no flag,
argument, or config key that widens the walk. Config lives in
``catalog.publish.yaml``.

    python3 tools/publish.py            # dry run: what would change
    python3 tools/publish.py --confirm  # upload; needs AWS credentials
    python3 tools/publish.py --confirm --force   # re-upload everything

Change detection compares local size and MD5 against the object's size and
ETag, so a normal publish uploads only what changed. The remote side is read
by listing each directory the catalog occupies *non-recursively*
(``--delimiter /``), concurrently. Listing write_prefix recursively instead
would walk every data object sharing it — the beta bucket holds ~67k COGs and
227 GiB of parquet under the same prefix — when only a couple thousand
metadata objects are ever published. Caveats, all inherited from what a bucket
listing can tell you:

- A listing carries no Content-Type, so a file whose bytes are unchanged but
  whose content-type mapping changed is skipped. Run ``--force`` after editing
  the content-type tables.
- Multipart-uploaded objects have a compound ETag that is not an MD5. Catalog
  files are small and upload in one part, so a compound ETag here means the
  object predates this publisher: it re-uploads to be safe.
- ``--force`` skips the listing entirely.

**It never deletes.** Removing a file from the published directory does not
unpublish it. Delete the object yourself if that is what you meant.

Uploads go through ``aws s3 cp`` (the CLI is what rails has; boto3 is not a
dependency) on a bounded thread pool, each with retries — a single flaky PUT
(``IncompleteBody``) used to abort a multi-thousand-object publish and leave
every later object stale. If the listing fails, every file is treated as
changed and a dry run still works offline. It never silently skips.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "catalog.publish.yaml"

# Values the portolan-catalog-template ships with. Publishing while any of
# these survives would upload to a bucket that is not yours, so it is refused
# before any AWS call. This catalog's config is real, so the guard is a
# regression check, not a setup gate.
SENTINELS = ("EXAMPLE-BUCKET", "EXAMPLE-PREFIX", "example.invalid")

# Where rails keeps the aws CLI (compute and login nodes both), tried when
# `aws` is not already on PATH.
RAILS_AWS = "/u/cholmes/micromamba/envs/ftw/bin/aws"

_CT_BY_NAME = {
    "catalog.json": "application/json",
    "collection.json": "application/json",
}
_CT_BY_SUFFIX = {
    ".json": "application/geo+json",  # items; catalog/collection by name above
    ".geojson": "application/geo+json",
    ".parquet": "application/vnd.apache.parquet",
    ".pmtiles": "application/vnd.pmtiles",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/markdown; charset=utf-8",  # llms.txt
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff; application=geotiff",
    ".tiff": "image/tiff; application=geotiff",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}
DEFAULT_TYPE = "application/octet-stream"

# MapLibre style documents are .json but carry a more specific type, which is
# what lets a browser dispatch on them.
STYLE_TYPE = "application/vnd.mapbox.style+json"

# A catalog is thousands of small JSON objects, and one round trip to
# us-west-2 costs ~240 ms. A serial loop leaves the link idle for that whole
# time; 16 workers moved 1,786 objects in 46 s where serial took minutes.
MAX_UPLOAD_WORKERS = 16

# Progress is printed every this many objects, not once per object.
PROGRESS_EVERY = 100

# .portolan/ is skipped as a dot-directory except for this one file, which is
# Portolan catalog metadata and publishes with the catalog.
PORTOLAN_METADATA = Path(".portolan/metadata.yaml")


@dataclass(frozen=True)
class Upload:
    """One local file and the object key it publishes to."""

    local: Path
    key: str
    content_type: str


def load_config(path: Path = CONFIG) -> dict[str, str]:
    """Read the flat scalar map in catalog.publish.yaml.

    Deliberately not a YAML parser. The file is a flat map of strings by
    design, so the publisher has no dependencies at all. Do not add nesting
    to it.
    """
    config: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        config[key.strip()] = value.strip().strip("'\"")
    missing = {"write_prefix", "public_base", "publish_dir"} - config.keys()
    if missing:
        sys.exit(f"{path.name} is missing: {', '.join(sorted(missing))}")
    return config


def split_s3_uri(uri: str) -> tuple[str, str]:
    """Split s3://bucket/some/prefix into ("bucket", "some/prefix")."""
    if not uri.startswith("s3://"):
        sys.exit(f"write_prefix must start with s3:// — got {uri!r}")
    rest = uri[len("s3://"):].strip("/")
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        sys.exit(f"write_prefix names no bucket — got {uri!r}")
    return bucket, prefix


def unedited_sentinels(config: dict[str, str]) -> list[str]:
    """Sentinel values still present in the config, if any."""
    blob = f"{config['write_prefix']} {config['public_base']}"
    return [s for s in SENTINELS if s in blob]


def content_type_for(path: Path) -> str:
    """The Content-Type an object gets, by name, suffix, and location."""
    if path.suffix == ".json" and (
        path.name.endswith(".style.json") or "styles" in path.parts
    ):
        return STYLE_TYPE
    if path.name in _CT_BY_NAME:
        return _CT_BY_NAME[path.name]
    return _CT_BY_SUFFIX.get(path.suffix.lower(), DEFAULT_TYPE)


def is_publishable(rel: Path) -> bool:
    """False for paths the publisher skips inside the published directory.

    Dotfiles and dot-directories are skipped, which covers .gitkeep and
    Portolan tooling state (.portolan/config.yaml, .portolan/state.json).
    The one exception is .portolan/metadata.yaml, which is catalog metadata
    and publishes with the catalog.
    """
    if rel == PORTOLAN_METADATA:
        return True
    return not any(part.startswith(".") for part in rel.parts)


def collect_uploads(config: dict[str, str], root: Path = ROOT) -> list[Upload]:
    """Every file that would be uploaded, in sorted order.

    The walk is rooted at publish_dir and nothing else. This is the function
    that makes "nothing outside the published directory is published" true.
    """
    _, prefix = split_s3_uri(config["write_prefix"])
    base = root / config["publish_dir"]
    if not base.is_dir():
        sys.exit(f"publish_dir does not exist: {base}")
    uploads = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if not is_publishable(rel):
            continue
        key = f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
        uploads.append(Upload(path, key, content_type_for(path)))
    return uploads


def md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - matches S3 ETag, not a security use
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_unchanged(
    upload: Upload,
    index: dict[str, tuple[int, str]],
    multipart_matches_on_size: bool = False,
) -> bool:
    """True when the object already matches the local bytes.

    A compound (multipart) ETag is not an MD5. For catalog metadata (small,
    single-part) a compound ETag means the object predates this publisher, so
    it re-uploads to be safe. Data uploads pass multipart_matches_on_size=True
    and compare a compound ETag on size alone — re-hashing a multi-gigabyte
    archive on every run is not viable, and re-uploading it is worse.
    """
    entry = index.get(upload.key)
    if entry is None:
        return False
    size, etag = entry
    if size != upload.local.stat().st_size:
        return False
    etag = etag.strip('"')
    if "-" in etag:
        return multipart_matches_on_size
    return etag == md5(upload.local)


def aws_cli() -> str | None:
    """The aws executable, or None when absent (dry runs tolerate that)."""
    found = shutil.which("aws")
    if found:
        return found
    if Path(RAILS_AWS).is_file():
        return RAILS_AWS
    return None


def key_dirs(uploads: list[Upload]) -> list[str]:
    """The distinct S3 "directory" prefixes the uploads live in.

    Each ends in "/" so the non-recursive listing matches exactly one
    directory level. A key at the bucket root (no "/") lists under "".
    """
    dirs = set()
    for upload in uploads:
        head, sep, _ = upload.key.rpartition("/")
        dirs.add(head + "/" if sep else "")
    return sorted(dirs)


def _list_dir(aws: str, bucket: str, prefix: str, region: str) -> list:
    """One non-recursive listing: the objects directly under prefix."""
    out = subprocess.run(
        [aws, "s3api", "list-objects-v2", "--bucket", bucket,
         "--prefix", prefix, "--delimiter", "/", "--region", region,
         "--output", "json", "--query", "Contents[].[Key,Size,ETag]"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out) or []


# Above this many distinct directories, per-directory listing costs more
# subprocesses than one recursive listing costs pages: the committed raster
# item tree alone is ~71k directories, and a recursive listing of its
# top-level prefixes is a few hundred paginated calls.
RECURSIVE_LISTING_THRESHOLD = 64


def remote_index_recursive(
    uploads: list[Upload], config: dict[str, str],
) -> dict[str, tuple[int, str]] | None:
    """One recursive listing per top-level prefix, via boto3.

    Returns None when boto3 is unavailable (caller falls back to the
    per-directory walk) and {} when a listing fails — the same
    err-toward-upload contract as the per-directory path.
    """
    try:
        import boto3
    except ImportError:
        return None
    bucket, prefix = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    tops = set()
    for u in uploads:
        rel = u.key[len(prefix) + 1:] if prefix else u.key
        first, sep, _ = rel.partition("/")
        # A root-level file is its own prefix; a directory gets the slash so
        # "raster/" cannot also match a sibling named "raster.json".
        tops.add(f"{first}/" if sep else first)
    index: dict[str, tuple[int, str]] = {}
    s3 = boto3.client("s3", region_name=region)
    try:
        for top in sorted(tops):
            head = f"{prefix}/{top}" if prefix else top
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=head):
                for obj in page.get("Contents", []):
                    index[obj["Key"]] = (obj["Size"], obj["ETag"].strip('"'))
    except Exception as exc:  # noqa: BLE001 — same contract as remote_index
        print(f"note: could not list s3://{bucket}/{prefix} ({exc}); "
              "treating every file as changed")
        return {}
    return index


def remote_index(
    uploads: list[Upload], config: dict[str, str],
    workers: int = MAX_UPLOAD_WORKERS,
) -> dict[str, tuple[int, str]]:
    """Size and ETag for every published object, or {} when unreadable.

    Lists each directory the catalog occupies non-recursively, concurrently.
    Returns {} if any listing fails, so a dry run still works without
    credentials; every file then simply looks new, which errs toward
    uploading. It never silently skips. Past RECURSIVE_LISTING_THRESHOLD
    distinct directories (the committed raster item tree is ~71k of them),
    one recursive listing per top-level prefix replaces the walk.
    """
    if len(key_dirs(uploads)) > RECURSIVE_LISTING_THRESHOLD:
        recursive = remote_index_recursive(uploads, config)
        if recursive is not None:
            return recursive
        print("note: boto3 not available; falling back to per-directory "
              "listing — this will be slow for a tree this size")
    aws = aws_cli()
    if aws is None:
        print("note: aws CLI not found; treating every file as changed")
        return {}
    bucket, _ = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    index: dict[str, tuple[int, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_list_dir, aws, bucket, d, region): d
            for d in key_dirs(uploads)
        }
        for future in as_completed(futures):
            try:
                rows = future.result()
            except (subprocess.CalledProcessError, OSError) as exc:
                detail = (getattr(exc, "stderr", "") or str(exc)).strip()
                print(
                    f"note: could not list s3://{bucket}/{futures[future]} "
                    f"({detail}); treating every file as changed"
                )
                return {}
            for key, size, etag in rows:
                index[key] = (int(size), etag.strip('"'))
    return index


def upload_with_retry(
    upload: Upload, bucket: str, region: str, aws: str, retries: int = 4
) -> bool:
    """Upload one object via ``aws s3 cp``, retrying transient S3 failures.

    A single flaky PUT used to abort the whole run: ``aws s3 cp`` returns
    non-zero on a mid-transfer drop (IncompleteBody) and check=True propagated
    it, so a 3,000-object publish died a few hundred files in and every later
    object stayed stale. Retrying with backoff, and reporting the stragglers
    instead of raising, keeps one bad connection from stalling the catalog.
    """
    cmd = [aws, "s3", "cp", str(upload.local), f"s3://{bucket}/{upload.key}",
           "--region", region, "--content-type", upload.content_type]
    for attempt in range(1, retries + 2):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return True
        err = (proc.stderr or "").strip().splitlines()
        detail = err[-1][:160] if err else f"exit {proc.returncode}"
        if attempt > retries:
            print(f"  FAILED after {retries} retries: {upload.key}: {detail}",
                  file=sys.stderr, flush=True)
            return False
        wait = min(2 ** attempt, 30)
        print(f"  retry {attempt}/{retries} in {wait}s: {upload.key}: {detail}",
              flush=True)
        time.sleep(wait)
    return False


def upload_all(
    uploads: list[Upload], bucket: str, region: str, aws: str,
    retries: int = 4,
) -> list[str]:
    """Upload every object on a bounded pool. Returns the keys that failed.

    Every upload is attempted. One failure does not cancel the rest, so the
    caller reports all of them at once.
    """
    failures: list[str] = []
    done = 0
    total = len(uploads)
    with ThreadPoolExecutor(max_workers=MAX_UPLOAD_WORKERS) as pool:
        futures = {
            pool.submit(upload_with_retry, u, bucket, region, aws, retries): u
            for u in uploads
        }
        for future in as_completed(futures):
            upload = futures[future]
            done += 1
            if not future.result():
                failures.append(upload.key)
            if done % PROGRESS_EVERY == 0 or done == total:
                print(f"  {done}/{total} done", flush=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync the published directory to object storage, 1:1.",
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
        "--only", action="append", metavar="SUBDIR",
        help="publish only files under this publish_dir subdirectory "
             "(repeatable). For a staged rollout where part of the tracked "
             "tree is known to be behind the published catalog — e.g. "
             "publishing raster/ while the vector tree awaits a merge.",
    )
    args = parser.parse_args()

    config = load_config()

    stale = unedited_sentinels(config)
    if stale:
        print("catalog.publish.yaml still carries template values:")
        for value in stale:
            print(f"  {value}")
        return 1

    bucket, prefix = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    uploads = collect_uploads(config)
    if args.only:
        heads = tuple(
            f"{prefix}/{sub.strip('/')}/" if prefix else f"{sub.strip('/')}/"
            for sub in args.only
        )
        uploads = [u for u in uploads if u.key.startswith(heads)]
        print(f"scoped to: {', '.join(s.strip('/') + '/' for s in args.only)}")
    if not uploads:
        print(f"nothing under {config['publish_dir']}/ to publish",
              file=sys.stderr)
        return 1

    index = {} if args.force else remote_index(uploads, config)
    changed = [u for u in uploads if args.force or not is_unchanged(u, index)]

    print(f"publish_dir: {config['publish_dir']}/")
    print(f"target:      s3://{bucket}/{prefix}")
    print(f"{len(uploads)} file(s) published, {len(changed)} to upload")
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
        print("\nRe-run to retry only these — uploads already made are "
              "skipped as unchanged.", file=sys.stderr)
        return 1
    print(f"\nuploaded {len(changed)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
