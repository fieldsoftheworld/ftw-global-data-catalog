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

Since the raster items were committed, that second mode has 134,394 bucket
hrefs to check — two per item across 67,197 items — which is half an hour of
HEAD requests aimed at one host on every run. So hrefs are **sampled per
family**, a family being one collection directory and one asset key
(``raster/2017`` × ``data``), with the sample spread across the family by
stride rather than taken from the front, so it spans every UTM zone instead
of clustering in zone 01. The gate prints how many it checked and how many
it sampled out, and ``LINK_SAMPLE=all`` checks every one (run that before
publishing). ``LINK_SAMPLE=N`` sets the per-family size.

Structural links are never sampled: every one of them resolves on disk, so
checking all 67,197 item links costs no network at all.

Run: python3 tests/test_links.py
"""
import json
import os
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
# `.thumb.png` is here, and plain `.png` deliberately is not: the per-item
# browse thumbnails are rendered on rails and live only in the bucket, while
# a collection's own `thumbnail.png` is committed and must resolve on disk.
DATA_SUFFIXES = (
    ".parquet", ".pmtiles", ".tif", ".tiff", ".copc.laz", ".laz", ".gpkg",
    ".zarr", ".geojsonl", ".shp", ".zip", ".thumb.png",
)
HEAD_WORKERS = 16
UA = "Mozilla/5.0 (ftw-global-data-catalog link check)"

# Per-family HEAD budget. "all" checks every href; an integer caps each
# (collection directory, asset key) family at that many.
_SAMPLE = os.environ.get("LINK_SAMPLE", "40")
SAMPLE: int | None = None if _SAMPLE == "all" else int(_SAMPLE)


def is_remote(href: str) -> bool:
    return "://" in href or href.startswith(("#", "mailto:"))


def is_data(href: str) -> bool:
    return href.lower().endswith(DATA_SUFFIXES)


def in_generated_tree(doc_path: Path, href: str) -> bool:
    """True when href resolves into the gitignored raster item tree."""
    target = (doc_path.parent / href).resolve()
    try:
        rel = target.relative_to(ROOT / "catalog" / "raster")
    except ValueError:
        return False
    return len(rel.parts) >= 2 and rel.parts[1].startswith("zone=")


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


def stac_documents() -> list[tuple[Path, dict]]:
    """Every STAC object under the published directory, parsed once.

    Parsed once and carried, not re-read per check: the raster items make
    this 67k files, and reading each twice doubled the gate's disk time for
    nothing.
    """
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
            out.append((path, doc))
    return out


def family(path: Path, key: str) -> tuple[str, ...]:
    """Which sampling family one href belongs to.

    The collection directory (the first two path components under the
    published root — ``raster/2017``, ``vector/2025``) plus the asset key.
    Every member of a family is the same kind of object produced by the same
    generator, which is what makes a sample of it meaningful.
    """
    return tuple(path.relative_to(BASE).parts[:2]) + (key,)


def sample(entries: list[tuple], limit: int | None) -> tuple[list, int]:
    """Cap each family at ``limit`` hrefs, spread across it by stride."""
    if limit is None:
        return entries, 0
    groups: dict[tuple, list] = {}
    for entry in entries:
        groups.setdefault(entry[0], []).append(entry)
    chosen: list[tuple] = []
    dropped = 0
    for _fam, members in sorted(groups.items()):
        if len(members) <= limit:
            chosen += members
            continue
        stride = len(members) / limit
        chosen += [members[int(i * stride)] for i in range(limit)]
        dropped += len(members) - limit
    return chosen, dropped


documents = stac_documents()
checked = 0
# (family, doc, asset key, url)
to_head: list[tuple[tuple[str, ...], Path, str, str]] = []

for path, doc in documents:
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
        # The generated raster item tree (zone=/gzd= catalogs and items) is
        # gitignored and rebuilt by `build_raster_items.py items`: a fresh
        # clone has the links but not the files, while the published bucket
        # has both. Treat a link into the absent generated tree like a data
        # href — HEAD-checked live, exempt under CI_LIGHT. See the
        # "generated item tree" section of docs/conformance.md.
        if is_data(href) or in_generated_tree(path, href):
            if CI_LIGHT:
                skipped += 1
            else:
                key = f"link:{link.get('rel')}"
                to_head.append((family(path, key), rel_path, key,
                                published_url(path, href)))
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
                to_head.append((family(path, key), rel_path, key,
                                published_url(path, href)))
            continue
        checked += 1
        errors.append(f"{rel_path}: asset {key} -> {href} does not exist")

if to_head:
    total = len(to_head)
    to_head, sampled_out = sample(to_head, SAMPLE)
    print(f"HEAD-checking {len(to_head)} of {total} data href(s) against "
          f"{PUBLIC_BASE} ...")
    with ThreadPoolExecutor(max_workers=HEAD_WORKERS) as pool:
        futures = {
            pool.submit(head_ok, url): (rel_path, key, url)
            for _fam, rel_path, key, url in to_head
        }
        for future in as_completed(futures):
            rel_path, key, url = futures[future]
            checked += 1
            detail = future.result()
            if detail is not None:
                errors.append(f"{rel_path}: asset {key} -> {url}: {detail}")
    if sampled_out:
        print(
            f"note   {sampled_out} further data href(s) sampled out "
            f"(LINK_SAMPLE={SAMPLE} per collection+asset family, spread by\n"
            "       stride). LINK_SAMPLE=all HEAD-checks every one; run that "
            "before publishing."
        )

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
