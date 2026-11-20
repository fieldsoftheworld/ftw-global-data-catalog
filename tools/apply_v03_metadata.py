#!/usr/bin/env python3
"""Apply the metadata this repository owns on top of the published catalog.

``catalog/`` now mirrors the published catalog (fetched 2026-11-20). Most of
it is written by the pipeline in ``taylor-geospatial/global-ftw-2e``, which is
private and whose generator changes are not in this repository, so
``build_vector_items.py`` and ``build_raster_items.py`` cannot reproduce the
current generation — they describe the previous one (54 zones a year, ~7,466
raster tiles a year). Until those changes are upstreamed, this script is how
the repository's own metadata gets re-applied after a tree is adopted:

    python3 tools/apply_v03_metadata.py            # apply, in place
    python3 tools/apply_v03_metadata.py --check    # fail if anything changed

It is idempotent by construction — every edit is "set this field to this
value" — so ``--check`` is a CI gate (``tests/test_v03_metadata.py``) that
fails when generated output drifts from what this file says it should be.

What it applies, and why:

* The Portolan v0.3.0 schema URI on every catalog and collection
  (PORTO-CORE-006, PORTO-CORE-009).
* The root catalog's title, its editions paragraph, and the ``child`` link to
  the 1st-edition catalog, plus the matching ``root`` link titles everywhere.
* The v0.3 partition rules on the vector collections: no glob in a data
  asset's href (PORTO-FMT-065), a listable absolute ``partition:glob``
  (PORTO-FMT-064/075), and the ``storage:schemes`` entry that names the
  endpoint it is listable through (PORTO-FMT-066). rashid resolves the glob
  through that entry — without it the listing is denied — so it is load
  bearing, not decorative.
* A ``title`` and a ``description`` on every asset (PORTO-CORE-088). The
  published tree leaves most of them bare on the seven re-generated years.
* The overview COG's media type and its datetime interval (PORTO-FMT-072).
* A licence line in any collection README that lacks one (PTL-FIL-005).

Text for the assets this repository already described is taken from
``build_vector_items.py`` itself, so the years it still generates (2017 and
2019) come out byte-identical to a generator run. The ``webcells-*`` assets
are new in the published tree; their description is quoted from
``pipeline/web_cells.py`` in this repository, which is the code that builds
them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_vector_items as vec  # noqa: E402
import build_raster_items as ras  # noqa: E402

CATALOG = ROOT / "catalog"
PORTOLAN_V03 = "https://schemas.portolan-sdi.org/portolan/v0.3.0/schema.json"
OLD_PORTOLAN = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"
ROOT_TITLE = "Fields of the World — Global Data"
OLD_ROOT_TITLES = (
    "Fields of the World — Global Data (2nd Edition)",
    "Fields of the World — Global",
)
FIRST_EDITION = {
    "rel": "child",
    "href": "https://data.source.coop/ftw/global-data/first-edition/catalog.json",
    "type": "application/json",
    "title": "Fields of the World — Global Data (1st Edition)",
}
LICENSE_LINE = ("Data licence: "
                "[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)")

#: Replaces the published root description's own first sentence, which says
#: what the catalog holds but not that it is the entry point for both
#: editions. Everything the publisher wrote after it is kept verbatim.
ROOT_DESCRIPTION_OPENING = (
    "The single entry point for Fields of the World (FTW) global "
    "field-boundary data. The collections in this catalog are the "
    "**2nd Edition**, the current release: per-year vector and raster "
    "collections of FTW global field-boundary predictions."
)
PUBLISHED_FIRST_SENTENCE = (
    "Fields of the World global field-boundary predictions, organized into "
    "per-year vector and raster collections."
)

ROOT_DESCRIPTION_TAIL = (
    "The **1st Edition** — 10 m PRUE predictions for 2024 and 2025, with "
    "prediction-confidence layers, probability Zarr and Sentinel-2 "
    "composites — is published separately, under "
    "`ftw/global-data/first-edition`, and hangs off this catalog as the "
    f"[{FIRST_EDITION['title']}]({FIRST_EDITION['href']}) sub-catalog. The "
    "two editions come from different models at different resolutions over "
    "different years, so they are alternatives rather than versions of one "
    "layer."
)

#: A5 resolution -> the zoom it draws at in `cells-{year}.pmtiles`.
#: `pipeline/web_cells.py`: "The published files use r4 to r11 at z0 to z7".
WEBCELL_ZOOM_OFFSET = 4


def webcells_text(key: str, year: int) -> tuple[str, str]:
    """Title and description for one `webcells-a5rN` asset.

    Every fact here is quoted from `pipeline/web_cells.py`, the script in
    this repository that builds these files.
    """
    resolution = int(key.removeprefix("webcells-a5r"))
    zoom = resolution - WEBCELL_ZOOM_OFFSET
    origin = ("bucketed straight from the parcel centroids"
              if resolution >= 7 else
              "bucketed from the centres of the r7 cells, because A5's "
              "hierarchy is not geometrically nested")
    return (
        f"A5 r{resolution} cell aggregates {year} (GeoParquet)",
        f"One A5 resolution of the {year} cell aggregates, r{resolution}, "
        f"{origin}. Columns `a5_cell`, `count`, `density` (fields per "
        "1,000 km²), `area_ha`, `avg_score`, `pct_covered`, `geometry`. "
        f"This is the layer drawn at zoom {zoom} of "
        f"`cells-{year}.pmtiles`; r7 is the same aggregate as "
        f"`cells_a5r7_{year}.parquet`."
    )


def asset_text(key: str, year: int, tree: str) -> tuple[str, str] | None:
    """(title, description) for one collection-level asset, or None."""
    specs = vec.style_specs()
    if key.startswith("webcells-a5r"):
        return webcells_text(key, year)
    if key.startswith("styles/"):
        name = key.removeprefix("styles/")
        spec = specs[name]
        default = " — default" if name == "coverage" else ""
        return (f"{spec['title']} ({year}){default}",
                "MapLibre GL style (spec v8) over this year's PMTiles. "
                + spec["description"].format(year=year))
    if tree == "vector":
        if key == "pmtiles":
            return (f"Fields {year} — A5 r7 cells z0–8 → field polygons "
                    "z9–13 (PMTiles)",
                    "The year as vector tiles for web maps: A5 r7 cell "
                    "aggregates from z0 to z8, handing over to the field "
                    "polygons themselves from z9 to z13. Layers `cells` and "
                    "`fields`; draw it with one of the `style` assets.")
        if key in ("cells", "cells-a5r7"):
            return (f"A5 r7 cell aggregates {year} (GeoParquet)",
                    "Per-cell count, area_ha, avg_score, pct_covered — the "
                    "z0–8 exploration layer, also useful for analysis.")
        if key == "cells-pmtiles":
            return (f"A5 cell aggregates {year}, one resolution per zoom "
                    "(PMTiles)",
                    "A5 cells for drawing the year at global scale, with one "
                    "resolution per zoom: r4 at the globe to r11 at zoom 7. "
                    "Each cell carries count, area_ha, avg_score, "
                    "pct_covered and density (fields per 1,000 km²).")
        if key == "thumbnail":
            name = vec.THUMBNAIL_STYLE.get(year, "coverage")
            label = specs[name]["title"].split(" (")[0].lower()
            return (f"Fields {year} rendered with the {label} style",
                    f"Global preview of the {year} fields, rendered from "
                    f"this collection's PMTiles with the {label} style "
                    f"(`styles/{name}`).")
        if key == "mirror":
            return ("STAC GeoParquet mirror of the items",
                    "All item metadata in one stac-geoparquet file, for bulk "
                    "queries. Derived from the items; the item JSON stays "
                    "normative.")
    else:
        if key == "overview":
            return (f"Global field-probability overview {year} (COG)",
                    "The whole year mosaicked and colormapped at overview "
                    "resolution, so the collection renders at global scale. "
                    "8-bit RGB, JPEG-compressed, in Web Mercator "
                    "(EPSG:3857), with nodata 0 and an internal transparency "
                    "mask; the colormap is documented in this collection's "
                    "README.")
        if key == "thumbnail":
            return (f"Global field-probability overview {year}",
                    f"Small WebP render of the {year} overview COG, the card "
                    "image for this collection.")
        if key == "coverage":
            return (f"Tile coverage shaded by field fraction ({year})",
                    f"One square per published {year} tile, shaded by the "
                    "tile's field pixel fraction (`ftw:field_frac`), so the "
                    "map shows which MGRS tiles were processed and how much "
                    "field each holds. Drawn from `index/raster.parquet`, "
                    "not from the COGs.")
        if key == "mirror":
            return ("STAC GeoParquet mirror of the items",
                    "Every item's metadata in one stac-geoparquet file, for "
                    "bulk and spatial queries without fetching thousands of "
                    "item JSONs. Derived from the items; the item JSON stays "
                    "normative.")
    return None


def place_after(doc: dict, after: str, key: str, value) -> dict:
    """doc with key: value directly after `after`, order otherwise kept."""
    out: dict = {}
    for existing, existing_value in doc.items():
        if existing == key:
            continue
        out[existing] = existing_value
        if existing == after:
            out[key] = value
    if key not in out:
        out[key] = value
    return out


def fix_common(doc: dict) -> None:
    """Schema URI and `root` link titles — every catalog and collection."""
    doc["stac_extensions"] = [PORTOLAN_V03 if e == OLD_PORTOLAN else e
                              for e in doc.get("stac_extensions", [])]
    for link in doc.get("links", []):
        if link.get("rel") == "root" or link.get("title") in OLD_ROOT_TITLES:
            link["title"] = ROOT_TITLE


def fix_root(doc: dict) -> None:
    fix_common(doc)
    doc["title"] = ROOT_TITLE
    # Strip what this script added last time, then the publisher's own first
    # sentence, then rebuild. Idempotent, and it keeps every sentence the
    # publisher wrote after that first one.
    description = doc["description"]
    for part in (ROOT_DESCRIPTION_OPENING, ROOT_DESCRIPTION_TAIL,
                 PUBLISHED_FIRST_SENTENCE):
        description = description.replace(part, "").strip()
    doc["description"] = " ".join(
        [ROOT_DESCRIPTION_OPENING, description, ROOT_DESCRIPTION_TAIL])
    links = [l for l in doc["links"]
             if not (l.get("rel") == "child"
                     and "ftw/global-data/" in l.get("href", ""))]
    last_child = max(i for i, l in enumerate(links) if l.get("rel") == "child")
    links.insert(last_child + 1, dict(FIRST_EDITION))
    doc["links"] = links


def fix_vector_collection(path: Path, doc: dict, year: int) -> None:
    fix_common(doc)
    # PORTO-FMT-065: a data asset links to one file. The glob belongs in
    # partition:glob, which PORTO-FMT-064 wants absolute and listable.
    doc["assets"].pop("data", None)
    glob = f"s3://{vec.S3_BUCKET}/global-data-2e/vector/{year}/zone=*/utm*.parquet"
    doc["partition:glob"] = glob
    if vec.STORAGE_EXT not in doc["stac_extensions"]:
        doc["stac_extensions"].append(vec.STORAGE_EXT)

    count = doc.get("partition:file_count") or len(
        [l for l in doc["links"] if l.get("rel") == "item"])
    doc["partition:file_count"] = count
    head, sep, _ = doc["description"].partition("\n\n**Reading all ")
    doc["description"] = (head if sep else doc["description"]) + (
        f"\n\n**Reading all {count} partitions at once.** The glob is "
        f"`{glob}` (`partition:glob`), served anonymously by the "
        "[Source Cooperative data proxy](https://docs.source.coop/"
        "data-proxy) at endpoint `data.source.coop` — same keys as the "
        "HTTPS URLs, no credentials and no region. Expanding a wildcard "
        "needs a listing, which plain HTTPS does not offer, so the "
        "`s3://` form is the one to paste into DuckDB, GDAL or fsspec; "
        "`storage:schemes` carries the endpoint and the path-style "
        f"setting. In DuckDB: `{vec.DUCKDB_SECRET}` then "
        f"`SELECT zone, count(*) FROM read_parquet('{glob}', "
        "hive_partitioning=1) GROUP BY zone;` — `hive_partitioning=1` is "
        "what makes `zone` a column. One partition at a time is a plain "
        f"HTTPS read of `{vec.PUBLIC_BASE}/vector/{year}/zone=NN/"
        "utmNN.parquet`, and each partition is an item of this "
        "collection with its own size and checksum.")
    for key, asset in doc["assets"].items():
        text = asset_text(key, year, "vector")
        if text:
            asset["title"], description = text[0], text[1]
            doc["assets"][key] = place_after(asset, "title", "description",
                                             description)
    doc_out = place_after(doc, "partition:file_count", "storage:schemes",
                          vec.STORAGE_SCHEMES)
    write(path, doc_out)


def fix_raster_collection(path: Path, doc: dict, year: int) -> None:
    fix_common(doc)
    for key, asset in list(doc["assets"].items()):
        text = asset_text(key, year, "raster")
        if not text:
            continue
        asset["title"] = text[0]
        asset = place_after(asset, "title", "description", text[1])
        if key == "overview":
            # The COG media type the profile's table requires for a COG, and
            # the interval that lets a client match the overview to its items
            # (PORTO-FMT-072).
            asset["type"] = ras.COG_TYPE
            asset = place_after(asset, "roles", "start_datetime",
                                f"{year}-01-01T00:00:00Z")
            asset = place_after(asset, "start_datetime", "end_datetime",
                                f"{year}-12-31T23:59:59Z")
        doc["assets"][key] = asset
    write(path, doc)


def fix_item(path: Path, doc: dict, year: int) -> None:
    asset = doc["assets"]["data"]
    zone = int(doc["id"].removeprefix("utm"))
    asset["description"] = (
        f"One zone partition of the {year} collection: "
        f"{doc['properties']['table:row_count']:,} predicted field parcels "
        f"in UTM zone {zone}, WGS 84, "
        f"{vec.human_bytes(asset['file:size'])}. "
        "Columns are described in this item's `table:columns`.")
    doc["assets"]["data"] = place_after(asset, "title", "description",
                                        asset["description"])
    alternate = doc["assets"]["data"].get("alternate", {}).get("s3")
    if alternate:
        alternate["title"] = ("S3 URI via the Source Cooperative data proxy "
                              "(endpoint data.source.coop, anonymous read)")
    write(path, doc)


def fix_readme(path: Path) -> None:
    """PTL-FIL-005: a collection README states the licence."""
    text = path.read_text()
    if "CC-BY-4.0" in text or "creativecommons.org/licenses" in text:
        return
    lines = text.rstrip("\n").split("\n")
    lines += ["", LICENSE_LINE]
    path.write_text("\n".join(lines) + "\n")


def fix_summary_tables() -> None:
    """Correct the per-year parcel counts in the two summary tables.

    The published `catalog/README.md` and `catalog/vector/README.md` carry a
    `| Year | Processing | Raster tiles | Predicted parcels |` table whose
    last column disagrees with the collections for 2018, 2023, 2024 and 2025
    — by 20,091 to 23,544 parcels, always low. The collections, their year
    READMEs and the items themselves all agree with each other, so the table
    is the outlier. It is corrected here from the sum over the items, which
    is the measurement; rows that say "See collection" are left alone,
    because that is the publisher saying a year was not reprocessed.
    Reported upstream for the pipeline that writes these tables.
    """
    totals = {}
    for year_dir in sorted(d for d in (CATALOG / "vector").iterdir()
                           if d.is_dir() and d.name.isdigit()):
        totals[year_dir.name] = sum(
            json.loads(p.read_text())["properties"]["table:row_count"]
            for p in year_dir.glob("*/utm*.json"))

    pattern = re.compile(
        r"^(\| \[(\d{4})\][^|]*\|[^|]*\|[^|]*\| )([\d,]+)( \|)$", re.M)

    def fix_row(match: re.Match) -> str:
        measured = totals.get(match.group(2))
        if measured is None:
            return match.group(0)
        return f"{match.group(1)}{measured:,}{match.group(4)}"

    for path in (CATALOG / "README.md", CATALOG / "vector" / "README.md"):
        text = path.read_text()
        fixed = pattern.sub(fix_row, text)
        if fixed != text:
            path.write_text(fixed)


def write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any file would change")
    args = parser.parse_args()

    before = {p: p.read_bytes() for p in sorted(CATALOG.rglob("*"))
              if p.is_file()}

    fix_root_doc = json.loads((CATALOG / "catalog.json").read_text())
    fix_root(fix_root_doc)
    write(CATALOG / "catalog.json", fix_root_doc)

    for tree in ("vector", "raster"):
        tree_catalog = CATALOG / tree / "catalog.json"
        doc = json.loads(tree_catalog.read_text())
        fix_common(doc)
        write(tree_catalog, doc)

        for year_dir in sorted(d for d in (CATALOG / tree).iterdir()
                               if d.is_dir() and d.name.isdigit()):
            year = int(year_dir.name)
            collection = year_dir / "collection.json"
            doc = json.loads(collection.read_text())
            if tree == "vector":
                fix_vector_collection(collection, doc, year)
                for item in sorted(year_dir.glob("*/utm*.json")):
                    fix_item(item, json.loads(item.read_text()), year)
            else:
                fix_raster_collection(collection, doc, year)
            fix_readme(year_dir / "README.md")

    fix_summary_tables()

    changed = [p for p, body in before.items() if p.read_bytes() != body]
    new = [p for p in CATALOG.rglob("*")
           if p.is_file() and p not in before]
    for path in changed + new:
        print(f"  {path.relative_to(ROOT)}")
    print(f"{len(changed)} file(s) changed, {len(new)} added")
    if args.check and (changed or new):
        print("error  catalog/ is not what tools/apply_v03_metadata.py says "
              "it should be; run the script and commit the result")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
