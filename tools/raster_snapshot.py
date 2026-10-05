#!/usr/bin/env python3
"""Record the bucket's current ETags for catalog/raster/{year}/* as the snapshot.

``tools/publish.py`` overwrites a raster year file only while the bucket still
holds the version recorded here (see its docstring and CLAUDE.md). Run this
after inspecting the bucket's copies and folding any change into
``catalog/raster``. Read-only against the bucket; writes
``tools/raster_snapshot.json``.

Every local raster year key is recorded — ``null`` for one the bucket does not
hold — so a key missing from the file means the snapshot has rotted rather than
that the object is new. ``tests/test_publish.py`` fails on a missing key.

    AWS_PROFILE=source-coop AWS_ENDPOINT_URL=https://data.source.coop \\
        python3 tools/raster_snapshot.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish  # noqa: E402


def main() -> int:
    config = publish.load_config()
    _, prefix = publish.split_s3_uri(config["write_prefix"])
    uploads = [
        u for u in publish.collect_uploads(config)
        if publish.RASTER_YEAR_FILE.match(publish.rel_key(u.key, prefix))
    ]
    if not uploads:
        print("no catalog/raster/{year}/ files to record", file=sys.stderr)
        return 1
    try:
        index = publish.remote_index(uploads, config, strict=True)
    except publish.ListingError as exc:
        print(f"refusing to record: {exc}. A failed listing is not proof that "
              "nothing exists, and recording it would disarm the guard in "
              "publish.py. Fix access and retry.", file=sys.stderr)
        return 1
    snapshot: dict[str, str | None] = {}
    for upload in uploads:
        entry = index.get(upload.key)
        snapshot[publish.rel_key(upload.key, prefix)] = (
            None if entry is None else entry[1])
    publish.SNAPSHOT_FILE.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    absent = sum(1 for v in snapshot.values() if v is None)
    print(f"{len(snapshot)} key(s) recorded in {publish.SNAPSHOT_FILE} "
          f"({absent} absent from the bucket)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
