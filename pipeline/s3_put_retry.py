"""Multipart upload that retries each part, for files the Source Coop proxy keeps failing.

`aws s3 cp` gives up on the whole file when one UploadPart returns 520 (botocore does not
retry 520), so a large file can fail every pass. This retries the failed part only.
Needs boto3. Credentials come from ``--profile`` (default ``source-coop``; ``''`` uses the
environment). Parts are 16 MiB because the proxy answers 413 to larger single PUTs.

Every S3 call here goes through ``with_retries``: the proxy 520s arbitrary calls, so a
transient failure on the create or the verifying HEAD must not throw away a transfer that
took hours. The completing call is the one that cannot simply be retried — see ``complete``.

Objects are written with the Content-Type ``tools/publish.py`` assigns, so that an asset
uploaded here is served as the type its STAC entry declares.

Usage:
    python pipeline/s3_put_retry.py LOCAL s3://ftw/global-data-2e/vector/2025/zone=30/utm30.parquet
"""

import argparse
import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
try:
    # The catalog's one suffix -> Content-Type table, so an object uploaded here and the
    # same object uploaded by tools/upload_data.py are served identically.
    from publish import content_type_for
except ImportError:  # this script run as a standalone copy, outside the repo
    _CT_BY_SUFFIX = {
        ".pmtiles": "application/vnd.pmtiles",
        ".parquet": "application/vnd.apache.parquet",
        ".json": "application/geo+json",
    }

    def content_type_for(path: Path) -> str:
        return _CT_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


PART = 16 * 1024 * 1024  # the proxy 413s larger single PUTs
ATTEMPTS = 10


def put_part(s3, bucket: str, key: str, upload_id: str, path: Path, n: int) -> dict:
    with path.open("rb") as f:
        f.seek((n - 1) * PART)
        body = f.read(PART)

    def call() -> dict:
        r = s3.upload_part(Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=n, Body=body)
        return {"PartNumber": n, "ETag": r["ETag"]}

    return with_retries(call, f"part {n}")


def with_retries(call, what: str):
    "Run ``call()`` up to ATTEMPTS times with backoff; re-raise the last error."
    for i in range(ATTEMPTS):
        try:
            return call()
        except Exception as e:  # any transient proxy error
            if i == ATTEMPTS - 1:
                raise
            print(f"{what} attempt {i + 1}: {e}", flush=True)
            time.sleep(min(60, 5 * 2**i))
    raise AssertionError


def multipart_etag(parts: list[dict]) -> str | None:
    """The ETag S3 gives an object assembled from ``parts``: md5 of the part md5s, ``-N``.

    None when a part ETag is not a plain md5 (encryption, or a store that computes ETags
    its own way), which is the caller's cue to fall back to comparing sizes.
    """
    digests = b""
    for part in sorted(parts, key=lambda p: p["PartNumber"]):
        etag = part["ETag"].strip('"')
        if len(etag) != 32:
            return None
        try:
            digests += bytes.fromhex(etag)
        except ValueError:
            return None
    return f'"{hashlib.md5(digests).hexdigest()}-{len(parts)}"'


def landed(s3, bucket: str, key: str, parts: list[dict], size: int) -> bool:
    """True when the key already holds the object these parts assemble into.

    The ETag is what makes this an identity check and not just a size check: overwriting
    an object that happens to share the new one's size must not read as success.
    """
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception:  # absent, or the proxy failed this HEAD too
        return False
    if head["ContentLength"] != size:
        return False
    want = multipart_etag(parts)
    if want is None:
        return True
    if head.get("ETag") == want:
        return True
    print(
        f"s3://{bucket}/{key}: {size:,} bytes but ETag {head.get('ETag')} != {want} from the "
        "parts just uploaded; not treating it as this upload",
        flush=True,
    )
    return False


def complete(s3, bucket: str, key: str, upload_id: str, parts: list[dict], size: int) -> None:
    """Complete the multipart upload, asking the bucket what happened before each retry.

    CompleteMultipartUpload is *not* idempotent: once S3 has completed the upload the
    upload id is gone, so a second call answers NoSuchUpload. That matters because the
    failure this module exists for is the proxy losing a response S3 already acted on —
    retrying blindly then burns every attempt on 404s and reports a correct object as a
    failure, and the caller aborts and re-uploads tens of gigabytes for nothing. So on any
    error the object itself decides whether the call landed.
    """
    for i in range(ATTEMPTS):
        try:
            s3.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
            )
            return
        except Exception as e:
            if landed(s3, bucket, key, parts, size):
                print(
                    f"complete_multipart_upload attempt {i + 1}: {e}; the object is complete "
                    f"at {size:,} bytes, so the call landed and the response was lost",
                    flush=True,
                )
                return
            if i == ATTEMPTS - 1:
                raise
            print(f"complete_multipart_upload attempt {i + 1}: {e}", flush=True)
            time.sleep(min(60, 5 * 2**i))
    raise AssertionError


def upload(
    s3, local: Path, bucket: str, key: str, workers: int = 8, content_type: str | None = None
) -> tuple[int, int]:
    """Multipart-upload ``local``; returns (parts, bytes). Aborts the upload on any failure.

    Zero-byte files are refused: S3 multipart needs at least one part, so the upload would fail
    late with an opaque error after creating an upload id.
    """
    size = local.stat().st_size
    if size == 0:
        raise SystemExit(f"{local}: empty file; nothing to upload (multipart needs >= 1 byte)")
    n_parts = -(-size // PART)
    ct = content_type or content_type_for(local)
    upload_id = with_retries(
        lambda: s3.create_multipart_upload(Bucket=bucket, Key=key, ContentType=ct),
        "create_multipart_upload",
    )["UploadId"]
    try:
        with ThreadPoolExecutor(workers) as ex:
            parts = list(
                ex.map(
                    lambda n: put_part(s3, bucket, key, upload_id, local, n), range(1, n_parts + 1)
                )
            )
        complete(s3, bucket, key, upload_id, parts, size)
    except BaseException:
        # The abort goes through the same proxy that just failed the transfer, so it can
        # fail too. Letting it propagate would replace the error that explains the failure
        # with one about the cleanup, which is the diagnosis this module exists to print.
        # The orphaned upload id costs some storage until the bucket's lifecycle rule
        # reaps it; the lost error costs the next debugging session.
        try:
            s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except Exception as abort_error:  # noqa: BLE001 - the original error is the finding
            print(
                f"abort_multipart_upload for s3://{bucket}/{key} also failed: {abort_error}; "
                f"upload id {upload_id} is left open",
                flush=True,
            )
        raise
    remote = with_retries(lambda: s3.head_object(Bucket=bucket, Key=key), "head_object")[
        "ContentLength"
    ]
    if remote != size:
        raise SystemExit(f"s3://{bucket}/{key}: remote size {remote} != local {size}")
    return n_parts, size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("local", type=Path)
    ap.add_argument("dst")
    ap.add_argument("--profile", default="source-coop", help="'' to use env credentials")
    ap.add_argument("--endpoint", default="https://data.source.coop")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--content-type", default=None, help="default: the type tools/publish.py gives the suffix"
    )
    a = ap.parse_args()
    bucket, key = a.dst.removeprefix("s3://").split("/", 1)
    s3 = boto3.Session(profile_name=a.profile or None).client(
        "s3",
        endpoint_url=a.endpoint,
        region_name="us-east-1",
        config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
    )
    t0 = time.time()
    n_parts, size = upload(s3, a.local, bucket, key, a.workers, a.content_type)
    print(f"{a.dst}: {n_parts} parts, {size:,} bytes in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
