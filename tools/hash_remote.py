#!/usr/bin/env python3
"""Hash remote data files for ``file:size`` / ``file:checksum`` backfill.

Portolan (PORTO-CORE-028/029) requires every asset to carry ``file:size``
(byte count) and ``file:checksum`` encoded as a multihash: ``1220`` (sha2-256,
32 bytes) followed by the 64-char sha256 hex digest. The 2e data lives only
in the bucket, so the bytes have to be streamed to hash them. Run this on
rails, where the reads are cheap; the item builders then consume the sidecar.

    python3 tools/hash_remote.py urls.txt checksums.json --workers 12

``urls.txt`` holds one URL per line. The output JSON maps url ->
{"size": bytes, "checksum": "1220..."}. Resumable: URLs already present in
the output file are skipped, and the file is rewritten atomically after each
completion, so a killed run loses at most the transfers in flight.

Stdlib only (urllib), so it runs under any python3 on the cluster.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB
UA = "Mozilla/5.0 (ftw-global-data-catalog hash_remote)"


def hash_url(url: str, retries: int = 4) -> dict:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(req, timeout=120) as resp:
                expected = int(resp.headers.get("Content-Length") or 0)
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
            if expected and size != expected:
                raise OSError(f"read {size} of {expected} bytes")
            return {"size": size, "checksum": "1220" + digest.hexdigest()}
        except Exception as exc:  # noqa: BLE001 - retry any transfer failure
            if attempt == retries:
                raise
            wait = min(2 ** (attempt + 1), 30)
            print(f"  retry {attempt + 1}/{retries} in {wait}s: {url}: {exc}",
                  flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", type=Path, help="file with one URL per line")
    parser.add_argument("out", type=Path, help="output JSON sidecar")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    urls = [u.strip() for u in args.urls.read_text().splitlines() if u.strip()]
    done: dict[str, dict] = {}
    if args.out.exists():
        done = json.loads(args.out.read_text())
    todo = [u for u in urls if u not in done]
    print(f"{len(urls)} url(s), {len(done)} already hashed, {len(todo)} to go",
          flush=True)

    lock = threading.Lock()
    t0 = time.time()
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(hash_url, u): u for u in todo}
        for i, future in enumerate(as_completed(futures), 1):
            url = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - report at the end
                failed.append(url)
                print(f"  FAILED {url}: {exc}", file=sys.stderr, flush=True)
                continue
            with lock:
                done[url] = result
                tmp = args.out.with_suffix(".tmp")
                tmp.write_text(json.dumps(done, indent=1, sort_keys=True))
                tmp.replace(args.out)
            gib = sum(v["size"] for v in done.values()) / 2**30
            print(f"  [{i}/{len(todo)}] {url.rsplit('/', 1)[-1]} "
                  f"({gib:.1f} GiB total, {(time.time() - t0) / 60:.1f} min)",
                  flush=True)

    if failed:
        print(f"\n{len(failed)} url(s) failed; re-run to retry them",
              file=sys.stderr)
        return 1
    print(f"\nOK: {len(done)} url(s) hashed -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
