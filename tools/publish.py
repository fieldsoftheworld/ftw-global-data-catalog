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
would walk every data object sharing it — the 2e bucket holds ~67k COGs and
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

Two guards make ``--confirm`` refuse rather than guess (both lifted by ``--force``):

- **A failed listing aborts.** A dry run without credentials still works and
  shows every file as changed, but ``--confirm`` stops if ANY directory listing
  failed (or the aws CLI is missing). A failed listing is not evidence that the
  objects are absent.
- **Raster year files are bucket snapshots.** ``catalog/raster/{year}/*`` copies
  published objects whose per-zone catalogs are generated outside this repo
  (see CLAUDE.md). Each is overwritten only if the object currently in the
  bucket still has the ETag recorded in ``tools/raster_snapshot.json`` when the
  snapshot was taken; a bucket object that changed since (or a new local file
  that would replace an existing remote one) is refused. Re-record the snapshot
  with ``python3 tools/raster_snapshot.py`` after inspecting the bucket's copy.

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
import re
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
    ".txt": "text/plain; charset=utf-8",
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


class ListingError(RuntimeError):
    """A remote listing failed, so what exists in the bucket is unknown."""


def remote_index(
    uploads: list[Upload], config: dict[str, str],
    workers: int = MAX_UPLOAD_WORKERS, strict: bool = False,
) -> dict[str, tuple[int, str]]:
    """Size and ETag for every published object, or {} when unreadable.

    Lists each directory the catalog occupies non-recursively, concurrently.
    Returns {} if any listing fails, so a dry run still works without
    credentials; every file then simply looks new, which errs toward
    uploading. It never silently skips. With ``strict`` (used for
    ``--confirm``) a failed listing or a missing aws CLI raises ListingError
    instead: an unreadable bucket must not be read as an empty one.
    """
    aws = aws_cli()
    if aws is None:
        if strict:
            raise ListingError("aws CLI not found; cannot list the bucket")
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
                if strict:
                    raise ListingError(
                        f"could not list s3://{bucket}/{futures[future]} "
                        f"({detail})"
                    ) from exc
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


SNAPSHOT_FILE = ROOT / "tools" / "raster_snapshot.json"
RASTER_YEAR_FILE = re.compile(r"^raster/\d{4}/[^/]+$")


def load_snapshot(path: Path = SNAPSHOT_FILE) -> dict[str, str]:
    """{key relative to write_prefix: MD5 the bucket object had when snapshotted}."""
    return json.loads(path.read_text()) if path.is_file() else {}


def snapshot_conflicts(
    changed: list[Upload], index: dict[str, tuple[int, str]], prefix: str,
    snapshot: dict[str, str],
) -> list[str]:
    """Keys under raster/{year}/ that would overwrite a bucket object we did not snapshot.

    Local files there are copies of published objects. Overwriting is fine
    while the bucket still holds the version that was copied (its ETag equals
    the recorded MD5). If it holds anything else, someone published since, and
    uploading would silently revert that. Keys absent from the bucket are new
    and fine.
    """
    out = []
    for upload in changed:
        rel = upload.key[len(prefix) + 1:] if prefix else upload.key
        if not RASTER_YEAR_FILE.match(rel) or upload.key not in index:
            continue
        remote_etag = index[upload.key][1].strip('"')
        if snapshot.get(rel) != remote_etag:
            out.append(upload.key)
    return sorted(out)


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
    if not uploads:
        print(f"nothing under {config['publish_dir']}/ to publish",
              file=sys.stderr)
        return 1

    try:
        index = {} if args.force else remote_index(
            uploads, config, strict=args.confirm)
    except ListingError as exc:
        print(f"refusing to upload: {exc}. A failed listing is not proof "
              "that nothing exists. Fix access and retry, or pass --force "
              "to upload without looking.", file=sys.stderr)
        return 1
    changed = [u for u in uploads if args.force or not is_unchanged(u, index)]
    conflicts = [] if args.force else snapshot_conflicts(
        changed, index, prefix, load_snapshot())

    print(f"publish_dir: {config['publish_dir']}/")
    print(f"target:      s3://{bucket}/{prefix}")
    print(f"{len(uploads)} file(s) published, {len(changed)} to upload")
    print("this never deletes; removing a file here does not unpublish it")

    if conflicts:
        print(f"{len(conflicts)} raster year file(s) differ in the bucket from "
              "the recorded snapshot and would NOT be overwritten:",
              file=sys.stderr)
        for key in conflicts:
            print(f"  {key}", file=sys.stderr)
        if args.confirm:
            print("refusing to upload. Inspect the bucket's copies, merge "
                  "them into catalog/raster, re-record the snapshot "
                  "(tools/raster_snapshot.py), or pass --force.",
                  file=sys.stderr)
            return 1

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
