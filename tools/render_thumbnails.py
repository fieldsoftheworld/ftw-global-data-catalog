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

Each render is gated against a background-only render of **the same frame**,
compared pixel by pixel: an image that draws over less than `MIN_INK` of the
blank one drew no data and is not written. Comparing byte counts instead
cannot do that job — a solid-colour 1024 px PNG still encodes to about 3 KB,
comfortably more than any threshold a smaller blank probe suggests, so a dead
tile source or a style that resolves to nothing would have been written and
then stamped. Re-run `tools/build_vector_items.py` afterwards to refresh
`file:size` and `file:checksum`, and `tests/test_thumbnails.py` to check that
each image really is its own style's render.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from png_stats import diff_fraction  # noqa: E402

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
# Measured over the nine accepted renders: each draws over 0.149-0.152 of the
# frame. The floor is well under that and well over nothing at all, so it
# catches an empty render without tracking how much land each year covers.
# tests/test_thumbnails.py holds the same floor for the committed images.
MIN_INK = 0.05
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
        raise ValueError(f"{url}: not a PMTiles archive")
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


def render_year(year: int, name: str) -> str | None:
    """Render one year, or return why it was rejected.

    A rejected year leaves the committed image alone and does not stop the
    others: aborting mid-loop would leave the early years rewritten and the
    late years stale, which is a worse catalog than either end of the run.
    """
    style_path = VECTOR / str(year) / "styles" / f"{name}.json"
    style = json.loads(style_path.read_text())
    url = next(iter(style["sources"].values()))["url"].removeprefix("pmtiles://")
    minzoom, maxzoom = pmtiles_zooms(url)

    image = post_clip(render_style(style, minzoom, maxzoom), BBOX, SIZE)
    blank = post_clip(
        {"version": 8, "sources": {}, "layers": [WHITE]}, BBOX, SIZE
    )
    ink = diff_fraction(image, blank)
    if ink < MIN_INK:
        return (f"{year}: render draws over {ink:.4f} of the blank frame "
                f"(floor {MIN_INK}) — no data reached the renderer")

    dest = VECTOR / str(year) / "thumbnail.png"
    dest.write_bytes(image)
    print(f"{year}: {name} -> {dest.relative_to(ROOT)} "
          f"({len(image) / 1024:.0f} KB, ink {ink:.3f})")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, choices=sorted(STYLE_BY_YEAR))
    args = parser.parse_args()
    years = [args.year] if args.year else sorted(STYLE_BY_YEAR)
    failures = []
    for year in years:
        try:
            problem = render_year(year, STYLE_BY_YEAR[year])
        except Exception as exc:  # noqa: BLE001 - one bad year, not the run
            problem = f"{year}: {exc}"
        if problem:
            print(f"error  {problem}", file=sys.stderr)
            failures.append(year)
    if failures:
        print(f"error  {len(failures)} year(s) not written: "
              + ", ".join(str(y) for y in failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
