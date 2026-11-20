#!/usr/bin/env python3
"""catalog/ carries the metadata tools/apply_v03_metadata.py says it should.

`catalog/` mirrors the published catalog, and most of it is written by a
pipeline this repository does not contain. The fields this repository owns on
top of that — the v0.3 schema URI, the root's title and 1st-edition child
link, the partition glob and its storage scheme, every asset's title and
description, the overview's media type and datetimes — are applied by
`tools/apply_v03_metadata.py`, which is idempotent.

So the gate is a re-run: apply the script in `--check` mode and fail if it
would change anything. A hand-edit to generated output, or a freshly adopted
tree that nobody re-applied, fails the build instead of surviving until the
next person wonders why the catalog disagrees with itself.

Run: python3 tests/test_v03_metadata.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

result = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "apply_v03_metadata.py"), "--check"],
    capture_output=True, text=True, cwd=ROOT,
)
print(result.stdout.rstrip())
if result.stderr.strip():
    print(result.stderr.rstrip(), file=sys.stderr)
if result.returncode != 0:
    print("       run: python3 tools/apply_v03_metadata.py")
    raise SystemExit(1)
print("OK: catalog/ matches tools/apply_v03_metadata.py")
