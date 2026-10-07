#!/usr/bin/env python3
"""Regenerating the vector tree is a no-op, not an asset drop.

`tools/build_vector_items.py` says "edit the generator, never the generated
JSON", so a re-run has to reproduce what is committed. The `pmtiles`, `cells`
and `styles/*` assets are gated on `pmtiles_{year}` being present in
`staging-data/checksums/tiles_meta.json`, and that sidecar is gitignored — so
in a fresh checkout, or one whose sidecar covers only some years, the generator
has to fall back to the sizes and checksums already committed. Without the
fallback a re-run silently deletes those assets and the whole styles subtree.

No network, no AWS, no credentials.

Run: python3 tests/test_build_vector_items.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_vector_items as gen  # noqa: E402

errors: list[str] = []

COMMITTED = {
    "assets": {
        "pmtiles": {"file:size": 25523209031, "file:checksum": "1220aa"},
        "cells": {"file:size": 1651038, "file:checksum": "1220bb"},
        "mirror": {"file:size": 34419, "file:checksum": "1220cc"},
        "data": {"href": "./zone=*/utm*.parquet"},
    },
}

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "vector"
    (out / "2018").mkdir(parents=True)
    (out / "2018" / "collection.json").write_text(json.dumps(COMMITTED))
    sidecar = Path(tmp) / "tiles_meta.json"
    original = gen.TILES_META
    try:
        # No sidecar at all: every entry comes from the committed collection.
        gen.TILES_META = sidecar
        meta = gen.tiles_meta_for(2018, out)
        for key, asset in COMMITTED["assets"].items():
            if key == "data":
                continue
            entry = meta.get(f"{key}_2018")
            if entry != {"size": asset["file:size"],
                         "checksum": asset["file:checksum"]}:
                errors.append(f"no sidecar: {key}_2018 is {entry}, expected "
                              "the committed size and checksum")

        # The sidecar is written by the PMTiles build, so it wins where it has
        # the year. The committed values still fill the years it does not.
        sidecar.write_text(json.dumps(
            {"pmtiles_2018": {"size": 42, "checksum": "1220ff"}}))
        meta = gen.tiles_meta_for(2018, out)
        if meta.get("pmtiles_2018") != {"size": 42, "checksum": "1220ff"}:
            errors.append(f"sidecar did not win: {meta.get('pmtiles_2018')}")
        if meta.get("cells_2018", {}).get("size") != 1651038:
            errors.append("a sidecar with one key dropped the committed "
                          f"cells entry: {meta.get('cells_2018')}")

        # A year with nothing committed and nothing in the sidecar stays empty,
        # rather than inventing values.
        if gen.tiles_meta_for(1999, out).get("pmtiles_1999") is not None:
            errors.append("an unknown year invented a pmtiles entry")
    finally:
        gen.TILES_META = original

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: tiles_meta falls back to the committed assets")
