"""Upload a year's multi-resolution hexagon archive to Source Cooperative.

    python pipeline/upload_cells.py 2025 cells/cells-2025.pmtiles

Sends the file to s3://ftw/global-data-2e/vector/{year}/cells-{year}.pmtiles (``--name`` picks another object
name) through the proxy (``s3_put_retry``: 16 MiB parts, each retried on 520), then checks size and the S3
multipart ETag against the local file. By default it refuses to run if that key already exists (nothing is
overwritten). ``--replace-existing`` is the explicit opt-in for replacing an object: it prints the old size and
ETag first (keep a local copy of the old file), then uploads over it.
Credentials: the ``source-coop`` AWS profile. The object gets the Content-Type
``tools/publish.py`` assigns ``.pmtiles``.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).parent))
from s3_put_retry import PART, multipart_etag, upload, with_retries

BUCKET = "ftw"


def local_etag(path: Path) -> str:
    """The multipart ETag S3 gives ``path`` when sent in PART-sized parts."""
    parts = []
    with path.open("rb") as f:
        while chunk := f.read(PART):
            parts.append(
                {
                    "PartNumber": len(parts) + 1,
                    "ETag": hashlib.md5(chunk, usedforsecurity=False).hexdigest(),
                }
            )
    etag = multipart_etag(parts)
    assert etag is not None
    return etag


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("year", type=int)
    ap.add_argument("local", type=Path)
    ap.add_argument("--profile", default="source-coop")
    ap.add_argument("--endpoint", default="https://data.source.coop")
    ap.add_argument(
        "--name",
        default="cells-{year}.pmtiles",
        help="object name under vector/{year}/ ({year} is filled in)",
    )
    ap.add_argument(
        "--replace-existing",
        action="store_true",
        help="overwrite the object if it exists (prints its old size and ETag); the default refuses",
    )
    a = ap.parse_args()
    key = f"global-data-2e/vector/{a.year}/{a.name.format(year=a.year)}"
    s3 = boto3.Session(profile_name=a.profile or None).client(
        "s3",
        endpoint_url=a.endpoint,
        region_name="us-east-1",
        config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
    )
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            raise
    else:
        if not a.replace_existing:
            raise SystemExit(f"s3://{BUCKET}/{key} already exists; not overwriting (--replace-existing to replace)")
        old = s3.head_object(Bucket=BUCKET, Key=key)
        print(f"replacing s3://{BUCKET}/{key}: old size {old['ContentLength']:,} bytes, old ETag {old['ETag']}", flush=True)
    n_parts, size = upload(s3, a.local, BUCKET, key)
    head = with_retries(lambda: s3.head_object(Bucket=BUCKET, Key=key), "head_object")
    want = local_etag(a.local)
    ok = head["ContentLength"] == size and head.get("ETag") == want
    print(
        f"s3://{BUCKET}/{key}: {n_parts} parts, {size:,} bytes, ETag {head.get('ETag')} {'matches' if ok else 'DIFFERS from ' + want}"
    )
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
