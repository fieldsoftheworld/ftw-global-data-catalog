#!/usr/bin/env python3
"""Record the bucket's current ETags for catalog/raster/{year}/* as the snapshot.

``tools/publish.py`` overwrites a raster year file only while the bucket still
holds the version recorded here (see its docstring and CLAUDE.md). Run this after
inspecting the bucket's copies and folding any change into ``catalog/raster``.
Read-only against the bucket; writes ``tools/raster_snapshot.json``.

    AWS_PROFILE=source-coop AWS_ENDPOINT_URL=https://data.source.coop \\
        python3 tools/raster_snapshot.py
"""
import json
import sys

import publish


def main() -> int:
    config = publish.load_config()
    _, prefix = publish.split_s3_uri(config["write_prefix"])
    uploads = [u for u in publish.collect_uploads(config)
               if publish.RASTER_YEAR_FILE.match(u.key[len(prefix) + 1:])]
    index = publish.remote_index(uploads, config, strict=True)
    snapshot = {}
    for upload in uploads:
        entry = index.get(upload.key)
        if entry is not None:
            snapshot[upload.key[len(prefix) + 1:]] = entry[1]
    publish.SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(f"{len(snapshot)} object(s) recorded in {publish.SNAPSHOT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
