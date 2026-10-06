#!/usr/bin/env python3
"""Every asset's bytes are the bytes its metadata describes.

Two gates, both pure standard library so they run in a fresh clone:

- **Stamps match bytes.** For each asset href that resolves on disk,
  ``file:size`` is the file's size and ``file:checksum`` is its multihash
  ``1220`` + SHA-256. The generators stamp these from whatever is on disk, so
  nothing else notices when the two drift apart.
- **The vector thumbnails are the renders their titles claim.** Each of the
  nine collections titles its thumbnail "Fields {year} rendered with the
  {style} style". That sentence is checkable: the render's dominant colours
  are that style's own ramp and no other published style's.

The second gate exists because of a real failure. The commit that introduced
those titles captured them over the previous stand-in images — an 8 KB
matplotlib bar chart of UTM zones — and every gate passed, because
``file:size`` agreed with the bytes that were there. Size agreeing with the
bytes says nothing about whether the bytes are the right picture. Only a
human looking at the nine images caught it, and the fix (PR #18) would have
been a silent regression away from recurring: ``tools/make_index_thumbnails.py
vector`` used to rewrite exactly these paths with the chart.

Run: python3 tests/test_thumbnails.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from png_stats import dimensions, histogram  # noqa: E402
from publish import load_config  # noqa: E402
from render_thumbnails import STYLE_BY_YEAR  # noqa: E402

config = load_config()
BASE = ROOT / config["publish_dir"]
VECTOR = BASE / "vector"
RASTER = BASE / "raster"

MULTIHASH_SHA256 = "1220"

# A chiitiler world render of the field data. The stand-in charts it replaced
# were 8 KB; a solid-colour frame of this size encodes to about 3 KB. Both are
# an order of magnitude under the smallest real render (101 KB measured), so
# the floor separates them without tracking the exact byte counts.
MIN_THUMBNAIL_BYTES = 50 * 1024
# Measured across the nine accepted renders: 0.149-0.152 of the frame is not
# background. A frame that drew no data at all scores 0.
MIN_INK_FRACTION = 0.05
# How many of a style's colours must survive at better than 0.01% of the frame
# for the render to count as that style's. The nine score 4 or 5 on their own
# style and 0 on all three others.
MIN_PALETTE_HITS = 2
PALETTE_FLOOR = 10000  # one hit needs >= pixels/10000 of the frame

errors: list[str] = []


def stac_documents() -> list[tuple[Path, dict]]:
    """Every STAC object under the published directory.

    The generated raster item tree (``raster/{year}/zone=*/``) is skipped: it
    is gitignored, and by the policy in docs/conformance.md its assets are
    bucket-resident, so it holds nothing with local bytes to check.
    """
    out = []
    for path in sorted(BASE.rglob("*.json")):
        rel = path.relative_to(BASE)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(part.startswith("zone=") or part.startswith("gzd=")
               for part in rel.parts[:3]) and rel.parts[0] == "raster":
            continue
        if path.name.endswith(".style.json") or "styles" in path.parts:
            continue
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{rel}: invalid JSON ({exc})")
            continue
        if isinstance(doc, dict) and doc.get("type") in {
            "Catalog", "Collection", "Feature"
        }:
            out.append((path, doc))
    return out


def check_stamps(documents: list[tuple[Path, dict]]) -> int:
    """file:size and file:checksum against the bytes, where they are local."""
    checked = 0
    for path, doc in documents:
        rel = path.relative_to(ROOT)
        for key, asset in (doc.get("assets") or {}).items():
            href = asset.get("href", "")
            if not href or "://" in href or "*" in href:
                continue
            target = (path.parent / href).resolve()
            if not target.is_file():
                continue  # the link check owns whether it should exist
            checked += 1
            data = target.read_bytes()
            size = asset.get("file:size")
            if size is not None and size != len(data):
                errors.append(
                    f"{rel}: asset {key} file:size {size} != "
                    f"{len(data)} bytes on disk ({href})"
                )
            checksum = asset.get("file:checksum")
            if checksum:
                want = MULTIHASH_SHA256 + hashlib.sha256(data).hexdigest()
                if checksum != want:
                    errors.append(
                        f"{rel}: asset {key} file:checksum does not match "
                        f"the bytes on disk ({href})"
                    )
    return checked


def palette(year: int, name: str) -> set[tuple[int, int, int]]:
    """The #rrggbb literals in one published style document, as RGB."""
    path = VECTOR / str(year) / "styles" / f"{name}.json"
    literals = {m.lower() for m in re.findall(r"#[0-9a-fA-F]{6}",
                                              path.read_text())}
    return {tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in literals}


def claimed_style(year: int, doc: dict) -> str | None:
    """Which style the collection's own thumbnail title names.

    Read from the committed metadata rather than from STYLE_BY_YEAR, because
    the metadata is what a reader believes. The style assets carry the same
    labels the title is built from ("Field coverage (A5 r7 ...)" ->
    "...rendered with the field coverage style").
    """
    assets = doc.get("assets") or {}
    title = (assets.get("thumbnail") or {}).get("title", "")
    named = [
        key.split("/", 1)[1]
        for key, asset in assets.items()
        if key.startswith("styles/")
        and f"the {asset.get('title', '').split(' (')[0].lower()} style" in
        title.lower()
    ]
    if len(named) != 1:
        errors.append(
            f"vector/{year}/collection.json: thumbnail title {title!r} names "
            f"{len(named)} of the published styles, expected exactly one"
        )
        return None
    return named[0]


def check_vector_thumbnails() -> int:
    """One frame across the nine, and each one its own style's render."""
    frames: dict[tuple[int, int], list[int]] = {}
    checked = 0
    for year in sorted(STYLE_BY_YEAR):
        path = VECTOR / str(year) / "thumbnail.png"
        if not path.is_file():
            errors.append(f"vector/{year}/thumbnail.png: missing")
            continue
        checked += 1
        data = path.read_bytes()
        frames.setdefault(dimensions(data), []).append(year)
        if len(data) < MIN_THUMBNAIL_BYTES:
            errors.append(
                f"vector/{year}/thumbnail.png: {len(data)} bytes is under the "
                f"{MIN_THUMBNAIL_BYTES} floor — a stand-in chart or a blank "
                "frame, not a render of the data"
            )
            continue

        total, counts = histogram(data)
        ink = (total - counts.get((255, 255, 255), 0)) / total
        if ink < MIN_INK_FRACTION:
            errors.append(
                f"vector/{year}/thumbnail.png: only {ink:.3f} of the frame is "
                f"not background (floor {MIN_INK_FRACTION}) — the render drew "
                "little or no data"
            )
            continue

        collection = json.loads(
            (VECTOR / str(year) / "collection.json").read_text()
        )
        name = claimed_style(year, collection)
        if name is None:
            continue
        if name != STYLE_BY_YEAR[year]:
            errors.append(
                f"vector/{year}/collection.json: thumbnail title claims the "
                f"{name!r} style, render_thumbnails.STYLE_BY_YEAR says "
                f"{STYLE_BY_YEAR[year]!r}"
            )
            continue
        floor = total // PALETTE_FLOOR
        scores = {
            other: sum(1 for c in palette(year, other)
                       if counts.get(c, 0) >= floor)
            for other in sorted(STYLE_BY_YEAR.values())
        }
        mine = scores[name]
        rivals = {k: v for k, v in scores.items() if k != name and v >= mine}
        if mine < MIN_PALETTE_HITS or rivals:
            errors.append(
                f"vector/{year}/thumbnail.png: the title claims the {name!r} "
                f"style, but the image's colours score {scores} against the "
                f"published styles — it is not that style's render"
            )
    if len(frames) > 1:
        errors.append(
            "vector thumbnails do not share one frame: "
            + ", ".join(f"{w}x{h} for {years}" for (w, h), years
                        in sorted(frames.items()))
        )
    return checked


def check_raster_thumbnails() -> int:
    """The raster cards share one frame too, so the browser row is even."""
    frames: dict[tuple[int, int], list[str]] = {}
    checked = 0
    for path in sorted(RASTER.glob("*/thumbnail.png")):
        checked += 1
        frames.setdefault(dimensions(path.read_bytes()), []).append(
            path.parent.name
        )
    if len(frames) > 1:
        errors.append(
            "raster thumbnails do not share one frame: "
            + ", ".join(f"{w}x{h} for {years}" for (w, h), years
                        in sorted(frames.items()))
        )
    return checked


documents = stac_documents()
stamped = check_stamps(documents)
vector = check_vector_thumbnails()
raster = check_raster_thumbnails()

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)

print(f"OK: {stamped} local asset(s) match their file:size/file:checksum")
print(f"OK: {vector} vector thumbnail(s) are their own style's render, "
      f"{raster} raster thumbnail(s) share one frame")
