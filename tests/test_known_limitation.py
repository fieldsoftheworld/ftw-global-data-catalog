#!/usr/bin/env python3
"""The 2017/2024 under-detection note is in sync everywhere it is published.

tools/known_limitation.py owns the text. This gate fails when a managed location drifts
from it (someone edited one copy by hand), when a marker pair is missing, or when an
unaffected year carries the note. Fix with ``python3 tools/known_limitation.py``.

No network.

Run: python3 tests/test_known_limitation.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import known_limitation as kl  # noqa: E402

errors = kl.stale()

# the note says what it should: known limitation, under investigation, 2017 and 2024
if kl.TEXT:
    for needle in ("Known limitation", "under investigation", "2017", "2024"):
        if needle not in kl.TEXT:
            errors.append(f"TEXT lacks {needle!r}")
    if kl.YEARS != (2017, 2024):
        errors.append(f"YEARS {kl.YEARS} no longer matches the 2017 and 2024 wording in TEXT")

# strip/apply is idempotent and survives edits to the text and the wording
base = "Intro.\n\nMore."
once = kl.with_note(base, 2017)
if kl.TEXT and kl.with_note(once, 2017) != once:
    errors.append("applying the description note twice must change nothing")
if kl.with_note(base, 2020) != base:
    errors.append("unaffected years get no description note")
if kl.strip_note(once) != base:
    errors.append("strip_note must return the original description")
legacy = base + "\n\n**Known limitation, under investigation: old wording.**"
if kl.strip_note(legacy) != base:
    errors.append("the unmarked first-release note must still be stripped")
text = kl.TEXT
try:
    kl.TEXT = "A completely different sentence."
    changed = kl.with_note(once, 2017)
    if changed.count("different sentence") != 1 or "Known limitation" in changed:
        errors.append("editing TEXT must replace the old note, not add a second one")
finally:
    kl.TEXT = text

# retiring the note empties blocks but keeps the markers, so a later finding is one edit
text = kl.TEXT
try:
    kl.TEXT = ""
    sample = kl.replace_block(f"a\n{kl.block()}\nb")
    if sample is None or kl.BEGIN not in sample or "Known limitation" in sample:
        errors.append("an empty TEXT must keep the markers and drop the note")
    if kl.description_note(2017) != "":
        errors.append("an empty TEXT must add nothing to descriptions")
finally:
    kl.TEXT = text

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: known-limitation note in sync")
