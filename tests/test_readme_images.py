#!/usr/bin/env python3
"""Every image the root README shows exists under catalog/.

The README is rendered by Source Cooperative from the bucket root, so an image
has to be referenced by its public URL, and that URL is only valid once the file
under ``catalog/`` is published. This maps each public URL back to its local
file and fails when the file is missing, is not an image type the publisher
labels, is over the size budget, or has no alt text.

Run: python3 tests/test_readme_images.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import content_type_for, load_config  # noqa: E402

config = load_config()
BASE = ROOT / config["publish_dir"]
PUBLIC_BASE = config["public_base"].rstrip("/") + "/"
MAX_BYTES = 300 * 1024

text = (BASE / "README.md").read_text()
images = re.findall(r"!\[([^\]]*)\]\(([^)\s]+)\)", text)
errors: list[str] = []

if not images:
    errors.append("catalog/README.md shows no images")
for alt, url in images:
    if not alt.strip():
        errors.append(f"{url}: empty alt text")
    if not url.startswith(PUBLIC_BASE):
        errors.append(f"{url}: not under the public base {PUBLIC_BASE}")
        continue
    path = BASE / url[len(PUBLIC_BASE):]
    if not path.is_file():
        errors.append(f"{url}: {path.relative_to(ROOT)} does not exist")
        continue
    if not content_type_for(path).startswith("image/"):
        errors.append(f"{path.name}: published as {content_type_for(path)}")
    if path.stat().st_size > MAX_BYTES:
        errors.append(f"{path.name}: {path.stat().st_size} bytes, over {MAX_BYTES}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    sys.exit(1)
print(f"ok  {len(images)} README images resolve under {BASE.relative_to(ROOT)}/")
