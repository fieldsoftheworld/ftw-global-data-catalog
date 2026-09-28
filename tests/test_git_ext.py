#!/usr/bin/env python3
"""The root catalog points back at this repository.

The Portolan spec recommends `vcs` and `issues` links for a git-backed
catalog, and the alpha catalog's `git:*` fields ride along (non-spec extras;
rashid ignores them). Both hrefs are absolute, because the repository sits
outside the published catalog.

Run: python3 tests/test_git_ext.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import load_config  # noqa: E402

REPO = "https://github.com/fieldsoftheworld/ftw-global-data-catalog"

config = load_config()
doc = json.loads((ROOT / config["publish_dir"] / "catalog.json").read_text())

errors = []
if doc.get("git:repository") != REPO:
    errors.append("git:repository missing/wrong")
if doc.get("git:ref") != "main":
    errors.append("git:ref missing/wrong")
if doc.get("git:provider") != "github":
    errors.append("git:provider missing/wrong")

rels = {link.get("rel"): link.get("href") for link in doc.get("links", [])}
if rels.get("vcs") != REPO:
    errors.append("vcs link missing/wrong")
if rels.get("issues") != f"{REPO}/issues":
    errors.append("issues link missing/wrong")
self_href = rels.get("self", "")
if self_href != f"{config['public_base']}/catalog.json":
    errors.append(f"root self link is not absolute at public_base: {self_href}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: git extension fields, vcs/issues links, absolute root self link")
