#!/usr/bin/env python3
"""Every relative link and asset href resolves.

This catches the most common hand-edit mistake: adding a child link before the
directory it points at exists. Structural links always resolve on disk, so a
clean checkout checks them in milliseconds.

Data asset hrefs are different in this catalog: the metadata **overlays** the
existing bucket layout, so a data href like ``../utm01.parquet`` is never on
disk — the bytes live only at ``public_base``. Two modes:

- ``CI_LIGHT=1`` (CI): asset hrefs with a data suffix are skipped. The
  checkout holds the metadata and not the bytes, and CI should not depend on
  a third-party host being up.
- unset (locally, on rails): each data href is resolved against
  ``public_base`` and HEAD-checked over HTTP, concurrently. This is the real
  check that every advertised object actually exists in the bucket.

Run: python3 tests/test_links.py
"""
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import load_config  # noqa: E402

config = load_config()
BASE = ROOT / config["publish_dir"]
PUBLIC_BASE = config["public_base"].rstrip("/")

errors: list[str] = []
skipped = 0
globs = 0

CI_LIGHT = os.environ.get("CI_LIGHT") == "1"
# The exemption reads the suffix and nothing else. A directory rule or a path
# prefix rule widens on its own as the catalog grows. This tuple does not.
DATA_SUFFIXES = (
    ".parquet", ".pmtiles", ".tif", ".tiff", ".copc.laz", ".laz", ".gpkg",
    ".zarr", ".geojsonl", ".shp", ".zip",
)
# The raster year collections link their 54 zone catalogs (./zone=NN/catalog.json). Those
# catalogs, the 356 gzd catalogs under them and the 67k tile items are generated into the
# bucket by tooling that is not in this repo (see CLAUDE.md), so they are never on disk.
# Treated exactly like a data href: skipped under CI_LIGHT, HEAD-checked otherwise. The
# pattern is anchored to a year collection under raster/, so it widens for nothing else.
BUCKET_ONLY = re.compile(r"^\./zone=\d{2}/catalog\.json$")
HEAD_WORKERS = 16
UA = "Mozilla/5.0 (ftw-global-data-catalog link check)"


def is_remote(href: str) -> bool:
    return "://" in href or href.startswith(("#", "mailto:"))


def is_data(href: str) -> bool:
    return href.lower().endswith(DATA_SUFFIXES)


def is_bucket_only(path: Path, href: str) -> bool:
    "A raster year collection's link to a zone catalog that lives only in the bucket."
    rel = path.relative_to(BASE).parts
    return len(rel) == 3 and rel[0] == "raster" and bool(BUCKET_ONLY.match(href))


def published_url(doc_path: Path, href: str) -> str:
    """The public URL a relative href resolves to when published."""
    rel = PurePosixPath(
        os.path.normpath(doc_path.parent.joinpath(href).relative_to(BASE))
    )
    return f"{PUBLIC_BASE}/{rel.as_posix()}"


def head_ok(url: str) -> str | None:
    """None when the object exists; otherwise the failure detail."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            if resp.status == 200:
                return None
            return f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001 - any failure is a finding
        return str(exc)


def stac_documents() -> list[Path]:
    """Every STAC object under the published directory."""
    out = []
    for path in sorted(BASE.rglob("*.json")):
        if any(part.startswith(".") for part in path.relative_to(BASE).parts):
            continue
        if path.name.endswith(".style.json") or "styles" in path.parts:
            continue
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON ({exc})")
            continue
        if isinstance(doc, dict) and doc.get("type") in {
            "Catalog", "Collection", "Feature"
        }:
            out.append(path)
    return out


documents = stac_documents()
checked = 0
to_head: list[tuple[Path, str, str]] = []  # (doc, asset key, url)

for path in documents:
    doc = json.loads(path.read_text())
    rel_path = path.relative_to(ROOT)

    for link in doc.get("links", []):
        href = link.get("href", "")
        if not href or is_remote(href):
            continue
        if (path.parent / href).resolve().exists():
            checked += 1
            continue
        # A rel:pmtiles (or other data-suffix) link points at bytes that
        # live only in the bucket, exactly like a data asset href.
        if is_data(href) or is_bucket_only(path, href):
            if CI_LIGHT:
                skipped += 1
            else:
                to_head.append(
                    (rel_path, f"link:{link.get('rel')}", published_url(path, href))
                )
            continue
        checked += 1
        errors.append(
            f"{rel_path}: rel:{link.get('rel')} -> {href} does not exist"
        )

    for key, asset in (doc.get("assets") or {}).items():
        href = asset.get("href", "")
        if not href or is_remote(href):
            continue
        if (path.parent / href).resolve().exists():
            checked += 1
            continue
        if "*" in href:
            # A partition glob names a family, not an object; every member
            # is HEAD-checked through its item's own data asset.
            globs += 1
            continue
        if is_data(href):
            if CI_LIGHT:
                skipped += 1
            else:
                to_head.append((rel_path, key, published_url(path, href)))
            continue
        checked += 1
        errors.append(f"{rel_path}: asset {key} -> {href} does not exist")

if to_head:
    print(f"HEAD-checking {len(to_head)} data href(s) against "
          f"{PUBLIC_BASE} ...")
    with ThreadPoolExecutor(max_workers=HEAD_WORKERS) as pool:
        futures = {
            pool.submit(head_ok, url): (rel_path, key, url)
            for rel_path, key, url in to_head
        }
        for future in as_completed(futures):
            rel_path, key, url = futures[future]
            checked += 1
            detail = future.result()
            if detail is not None:
                errors.append(f"{rel_path}: asset {key} -> {url}: {detail}")

if skipped:
    print(
        f"note   {skipped} data href(s) not checked: CI_LIGHT is set and the "
        "bytes live in\n       object storage, not git. Run without CI_LIGHT "
        "locally to HEAD-check them."
    )

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)

note = f", {globs} glob(s) delegated to item checks" if globs else ""
print(f"OK: {checked} href(s) across {len(documents)} object(s){note}")
