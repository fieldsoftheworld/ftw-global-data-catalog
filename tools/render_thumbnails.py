#!/usr/bin/env python3
"""Render each vector collection's thumbnail from its own published style.

The browser card shows this image, so it should be the data as the catalog
styles it, not a stand-in. Every year has a PMTiles archive and four styles in
the bucket, and this renders one against the other through
[chiitiler](https://github.com/Kanahiro/chiitiler) (MapLibre GL Native,
server-side).

Years rotate through the four styles, so a row of cards reads as nine years of
one dataset rather than nine copies of one image. STYLE_BY_YEAR records the
assignment; `tools/build_vector_items.py` reads it back to title the asset.

Start the renderer first (Node 24.12 or newer, for `node:sqlite`):

    bash .../portolan-thumbnails/scripts/start_server.sh

Then:

    .venv/bin/python3 tools/render_thumbnails.py            # every year
    .venv/bin/python3 tools/render_thumbnails.py --year 2020

Each render is gated against a background-only render of the same frame: an
image that matches the blank one drew no data and is not written. Re-run
`tools/build_vector_items.py` afterwards to refresh `file:size` and
`file:checksum`.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VECTOR = ROOT / "catalog" / "vector"

# One style per year, cycling the four the catalog publishes. Coverage leads
# because it is the default style and the one the earlier thumbnails used.
STYLE_BY_YEAR = {
    2017: "coverage",
    2018: "count",
    2019: "avg-size",
    2020: "coverage",
    2021: "field-prob",
    2022: "count",
    2023: "avg-size",
    2024: "coverage",
    2025: "field-prob",
}

# The data's own extent. Antarctica and the high Arctic hold no fields, and
# cropping them keeps the populated latitudes legible in a small card.
BBOX = (-180.0, -58.0, 180.0, 78.0)
SIZE = 1024
PORT = 13579
UA = "Mozilla/5.0 (ftw-global-data-catalog thumbnails)"


def pmtiles_zooms(url: str) -> tuple[int, int]:
    """The archive's stored zoom range, from the PMTiles v3 header."""
    request = urllib.request.Request(
        url, headers={"Range": "bytes=0-126", "User-Agent": UA}
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        header = resp.read()
    if header[:7] != b"PMTiles":
        sys.exit(f"{url}: not a PMTiles archive")
    return header[100], header[101]


WHITE = {
    "id": "background",
    "type": "background",
    "paint": {"background-color": "#ffffff"},
}


def render_style(style: dict, minzoom: int, maxzoom: int) -> dict:
    """The published style, repointed at a form chiitiler can read.

    chiitiler resolves `pmtiles://` through a `tiles` template, not through
    the `url` key a browser client uses. Declaring the stored zoom range makes
    MapLibre overzoom the deepest tile rather than request one that is absent.
    """
    sources = {}
    for key, source in style.get("sources", {}).items():
        url = source["url"].removeprefix("pmtiles://")
        sources[key] = {
            "type": source.get("type", "vector"),
            "tiles": [f"pmtiles://{url}/{{z}}/{{x}}/{{y}}"],
            "minzoom": minzoom,
            "maxzoom": maxzoom,
        }
    # Symbol layers need a glyphs endpoint; without one the render worker dies.
    layers = [l for l in style["layers"] if l.get("type") != "symbol"]
    return {**style, "sources": sources, "layers": [WHITE, *layers]}


def post_clip(style: dict, bbox: tuple[float, ...], size: int) -> bytes:
    body = json.dumps({"style": style}).encode()
    box = ",".join(f"{v:g}" for v in bbox)
    url = f"http://localhost:{PORT}/clip.png?bbox={box}&size={size}&quality=100"
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=900) as resp:
        return resp.read()


def render_year(year: int, name: str) -> None:
    style_path = VECTOR / str(year) / "styles" / f"{name}.json"
    style = json.loads(style_path.read_text())
    url = next(iter(style["sources"].values()))["url"].removeprefix("pmtiles://")
    minzoom, maxzoom = pmtiles_zooms(url)

    image = post_clip(render_style(style, minzoom, maxzoom), BBOX, SIZE)
    blank = post_clip(
        {"version": 8, "sources": {}, "layers": [WHITE]}, BBOX, 256
    )
    if len(image) < len(blank) * 2:
        sys.exit(f"{year}: render is blank or near-blank ({len(image)} bytes)")

    dest = VECTOR / str(year) / "thumbnail.png"
    dest.write_bytes(image)
    print(f"{year}: {name} -> {dest.relative_to(ROOT)} "
          f"({len(image) / 1024:.0f} KB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, choices=sorted(STYLE_BY_YEAR))
    args = parser.parse_args()
    years = [args.year] if args.year else sorted(STYLE_BY_YEAR)
    for year in years:
        render_year(year, STYLE_BY_YEAR[year])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
