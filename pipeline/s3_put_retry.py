"""Multipart upload that retries each part, for files the Source Coop proxy keeps failing.

`aws s3 cp` gives up on the whole file when one UploadPart returns 520 (botocore does not
retry 520), so a large file can fail every pass. This retries the failed part only.
Needs boto3. Credentials come from ``--profile`` (default ``source-coop``; ``''`` uses the
environment). Parts are 16 MiB because the proxy answers 413 to larger single PUTs.

Usage:
    python pipeline/s3_put_retry.py LOCAL s3://ftw/global-data-2e/vector/2025/zone=30/utm30.parquet
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config

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


def upload(s3, local: Path, bucket: str, key: str, workers: int = 8) -> tuple[int, int]:
    """Multipart-upload ``local``; returns (parts, bytes). Aborts the upload on any failure.

    Zero-byte files are refused: S3 multipart needs at least one part, so the upload would fail
    late with an opaque error after creating an upload id.
    """
    size = local.stat().st_size
    if size == 0:
        raise SystemExit(f"{local}: empty file; nothing to upload (multipart needs >= 1 byte)")
    n_parts = -(-size // PART)
    upload_id = s3.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
    try:
        with ThreadPoolExecutor(workers) as ex:
            parts = list(
                ex.map(
                    lambda n: put_part(s3, bucket, key, upload_id, local, n), range(1, n_parts + 1)
                )
            )
        # the proxy can fail the completing call too; retrying it is safe (idempotent per upload id)
        with_retries(
            lambda: s3.complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
            ),
            "complete_multipart_upload",
        )
    except BaseException:
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise
    remote = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
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
    a = ap.parse_args()
    bucket, key = a.dst.removeprefix("s3://").split("/", 1)
    s3 = boto3.Session(profile_name=a.profile or None).client(
        "s3",
        endpoint_url=a.endpoint,
        region_name="us-east-1",
        config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
    )
    t0 = time.time()
    n_parts, size = upload(s3, a.local, bucket, key, a.workers)
    print(f"{a.dst}: {n_parts} parts, {size:,} bytes in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
