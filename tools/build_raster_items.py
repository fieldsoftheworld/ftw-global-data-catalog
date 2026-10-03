#!/usr/bin/env python3
"""Generate the raster tree of the 2e catalog.

``collections`` (this phase) emits, under ``catalog/raster/``:

- ``catalog.json`` — the raster subtree catalog (children: the 9 year
  collections).
- ``{year}/collection.json`` — one collection per year of field/boundary
  probability COGs, extents and measured numbers from
  ``index/raster.parquet``.
- README.md / AGENTS.md for the subtree and each collection.

The ~7,466 per-year items are NOT committed: Phase 4 of docs/plan.md adds an
``items`` subcommand that writes them straight to S3 at
``raster/{year}/{tile_key}.json``, next to each ``.tif``, plus per-item
thumbnails and the stac-geoparquet mirror. Until then the collections carry
no item links, and each collection's description says where the data is.

Band facts below are quoted from a verified ``gdalinfo`` of
``raster/2025/01KFS_0_0.tif`` (40032×40032 @ 2.5 m, bands ``field`` and
``boundary``, uint8 with scale 1/255, ZSTD COG, average-resampled
overviews, GDAL tags ``model=unet_balanced_fp32.onnx``,
``source_bands=B02,B03,B04,B08 x Q1-Q4``).

    .venv/bin/python3 tools/build_raster_items.py collections --out /tmp/raster-draft

LEGACY TEMPLATES: they still describe the flat ``raster/{year}/{tile_key}.tif`` layout. The
published tree is hive-partitioned with per-zone/per-gzd catalogs, and catalog/raster/{year}
now holds snapshots of the published files (see CLAUDE.md). ``--out`` is therefore required:
the default no longer points at catalog/raster, so a bare run cannot regress the snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PUBLIC_BASE = "https://data.source.coop/ftw/global-data-2e"
INDEX_URL = f"{PUBLIC_BASE}/index/raster.parquet"
YEARS = tuple(range(2017, 2026))

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"

MOSAICS_URL = "https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"
FTW_URL = "https://fieldsofthe.world"
REPO_URL = "https://github.com/fieldsoftheworld/ftw-global-data-catalog"

# Three ways in, and they are not interchangeable. The map renders the data;
# the Portolan browser walks the STAC tree and previews each asset; Source
# Cooperative lists the files to download. Earlier text called Source
# Cooperative "the data browser", which sent readers wanting a map to a
# directory listing.
VIEWER_URL = "https://research.taylorgeospatial.org/global-ftw-2e/web/"
BROWSER_BASE = "https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e"
SOURCE_COOP = "https://source.coop/ftw/global-data-2e"


def browser(path: str = "catalog.json") -> str:
    """The Portolan browser URL for one object in the published catalog."""
    return f"{BROWSER_BASE}/{path}"


def viewer(year: int | None = None) -> str:
    """The map URL, opened on one year. The app reads `year` from the hash."""
    return VIEWER_URL if year is None else f"{VIEWER_URL}#year={year}"


def folder(path: str = "") -> str:
    """The Source Cooperative page listing one directory of the catalog."""
    return f"{SOURCE_COOP}/{path}".rstrip("/")


def doc(path: str) -> str:
    """The raw URL of one published markdown file."""
    return f"{PUBLIC_BASE}/{path}"


# Prose links in these READMEs are absolute on purpose. Source Cooperative
# renders the markdown at /ftw/global-data-2e, where a relative `./raster/...`
# resolves to /ftw/raster/... and 404s, which is what readers reported. STAC
# `links` stay relative, as Portolan requires; only markdown prose changes.

PROVIDERS = [
    {
        "name": "Taylor Geospatial",
        "roles": ["producer", "licensor", "processor", "host"],
        "url": "https://taylorgeospatial.org/",
    },
]

_PROJECT = (
    f"Part of [Fields of the World]({FTW_URL}) — agricultural field "
    "boundaries delineated from Sentinel-2 imagery."
)

_BANDS = (
    "Each COG is 40,032 × 40,032 pixels at 2.5 m in its tile's UTM zone, "
    "with two uint8 bands scaled by 1/255: `field` (band 1, field-interior "
    "probability) and `boundary` (band 2, field-boundary probability). "
    "ZSTD-compressed COG layout with average-resampled overviews down to "
    "626 px. Produced by the `unet_balanced_fp32` FTW model from 16 input "
    "bands (B02/B03/B04/B08 × quarters Q1–Q4 of the year's "
    f"[Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}), 10 m); each "
    "COG's GDAL metadata records its four source mosaic tiles "
    "(`source_items`)."
)


def connect():
    import duckdb

    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-global-data-catalog)"}
    )
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET http_retries=20;")
    return con


def read_year_stats(con) -> dict[int, dict]:
    rows = con.execute(f"""
        SELECT year, count(*) AS n, sum(size_bytes) AS bytes,
               min(xmin), min(ymin), max(xmax), max(ymax),
               round(avg(field_frac), 4) AS mean_field_frac
        FROM '{INDEX_URL}' GROUP BY year ORDER BY year
    """).fetchall()
    out = {}
    for year, n, size, xmin, ymin, xmax, ymax, mff in rows:
        out[year] = {
            "n": n, "bytes": size, "bbox": [xmin, ymin, xmax, ymax],
            "mean_field_frac": mff,
        }
    return out


def build_collection(year: int, stats: dict) -> dict:
    tb = stats["bytes"] / 1e12
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT],
        "id": f"ftw-raster-{year}",
        "title": f"FTW Global — Field & Boundary Probabilities {year} (COG)",
        "description": (
            f"Field and boundary probability rasters for {year}: "
            f"{stats['n']:,} Cloud-Optimized GeoTIFFs at 2.5 m "
            f"({tb:.2f} TB), one per Sentinel-2 MGRS-based tile at "
            f"`raster/{year}/{{tile_key}}.tif`. Explore the year on the "
            f"[interactive map]({viewer(year)}), walk its metadata in the "
            f"[Portolan browser]({browser(f'raster/{year}/collection.json')}), "
            f"or download the files from "
            f"[Source Cooperative]({SOURCE_COOP}). {_PROJECT}\n\n"
            f"**The rasters.** {_BANDS}\n\n"
            f"Per-item STAC metadata is generated to the bucket next to "
            f"each COG; the [index manifest]({INDEX_URL}) lists every tile "
            "with href, size, bbox, and per-tile field/boundary/cropland "
            "pixel fractions. The pipeline that produced these COGs is "
            f"documented in [pipeline/README.md]"
            f"({REPO_URL}/blob/main/pipeline/README.md)."
        ),
        "license": "CC-BY-4.0",
        "keywords": ["agriculture", "field boundaries", "Fields of the World",
                     "FTW", "global", "Sentinel-2", "COG", "probability",
                     str(year)],
        "providers": PROVIDERS,
        "extent": {
            "spatial": {"bbox": [stats["bbox"]]},
            "temporal": {"interval": [[f"{year}-01-01T00:00:00Z",
                                       f"{year}-12-31T23:59:59Z"]]},
        },
        "summaries": {"gsd": [2.5]},
        "links": [
            {"rel": "root", "href": "../../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (2nd Edition)"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json"},
            {"rel": "license",
             "href": "https://creativecommons.org/licenses/by/4.0/",
             "type": "text/html", "title": "CC-BY-4.0"},
            {"rel": "derived_from", "href": MOSAICS_URL, "type": "text/html",
             "title": "TGE Labs Sentinel-2 quarterly cloudless mosaics"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Collection README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Collection agent guide"},
            # No rel:"via" for the map: Portolan reserves it for mirrored
            # source data (PTL-PRO-004), and this collection is the source.
            {"rel": "vcs", "href": REPO_URL, "type": "text/html",
             "title": "Catalog source repository (metadata and pipeline)"},
        ],
        "assets": {
            "thumbnail": {
                "href": "./thumbnail.png",
                "type": "image/png",
                "title": f"Tile coverage shaded by field fraction ({year})",
                "roles": ["thumbnail"],
            },
        },
    }


def build_raster_catalog(stats: dict[int, dict]) -> dict:
    children = [
        {"rel": "child", "href": f"./{year}/collection.json",
         "type": "application/json",
         "title": f"FTW Global — Field & Boundary Probabilities {year} (COG)"}
        for year in sorted(stats)
    ]
    total = sum(s["n"] for s in stats.values())
    tb = sum(s["bytes"] for s in stats.values()) / 1e12
    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT],
        "id": "raster",
        "title": "FTW Global (2nd Edition) — Field & boundary probability rasters",
        "description": (
            f"Per-year collections of field/boundary probability COGs at "
            f"2.5 m, {min(stats)}–{max(stats)}: {total:,} tiles, "
            f"{tb:.1f} TB. Explore them on the "
            f"[interactive map]({VIEWER_URL}) or in the "
            f"[Portolan browser]({browser('raster/catalog.json')}); the "
            f"files are listed on [Source Cooperative]({SOURCE_COOP}). "
            f"{_PROJECT}"
        ),
        "links": [
            {"rel": "root", "href": "../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (2nd Edition)"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Raster tree README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Raster tree agent guide"},
            {"rel": "vcs", "href": REPO_URL, "type": "text/html",
             "title": "Catalog source repository (metadata and pipeline)"},
            *children,
        ],
    }


# ── documentation ────────────────────────────────────────────────────────────

def _index_query(year: int) -> list[str]:
    return [
        "```python",
        "import duckdb",
        "con = duckdb.connect()",
        'con.execute("INSTALL httpfs; LOAD httpfs;")',
        f'idx = "{INDEX_URL}"',
        "con.sql(f\"\"\"",
        "    SELECT tile_key, href, round(field_frac, 3) AS field_frac",
        f"    FROM read_parquet('{{idx}}') WHERE year = {year}",
        "    ORDER BY field_frac DESC LIMIT 5",
        "\"\"\").show()",
        "```",
    ]


def year_readme(year: int, stats: dict) -> str:
    tb = stats["bytes"] / 1e12
    browser_year = browser(f"raster/{year}/collection.json")
    return "\n".join([
        f"# FTW Global — Field & Boundary Probabilities {year} (COG)", "",
        f"Field and boundary probability rasters for {year}: "
        f"**{stats['n']:,} Cloud-Optimized GeoTIFFs** at 2.5 m "
        f"({tb:.2f} TB), one per Sentinel-2 MGRS-based tile. {_PROJECT}", "",
        f"**[Open {year} on the interactive map]({viewer(year)})** to see the "
        f"predictions over imagery, or **[open it in the Portolan browser]"
        f"({browser_year})** to walk the metadata. The files are listed on "
        f"[Source Cooperative]({SOURCE_COOP}).", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## The rasters", "",
        _BANDS, "",
        "## Find tiles", "",
        f"The [index manifest]({INDEX_URL}) lists every tile with href, "
        "size, bbox, and per-tile `field_frac`/`boundary_frac`/"
        "`cropland_frac` pixel fractions. The five field-densest tiles of "
        f"{year}:", "",
        *_index_query(year), "",
        "## Read a tile", "",
        "```bash",
        f"gdalinfo /vsicurl/{PUBLIC_BASE}/raster/{year}/01KFS_0_0.tif",
        "```", "",
        "Any COG reader works over HTTP range requests; the overviews make "
        "low-zoom reads cheap.", "",
    ])


def year_agents(year: int, stats: dict) -> str:
    return "\n".join([
        f"# AGENTS.md — FTW probability rasters {year}", "",
        "Guidance for AI agents. Every claim here is quoted from a verified "
        "`gdalinfo` or measured from the index manifest.", "",
        f"- {stats['n']:,} COGs at `{PUBLIC_BASE}/raster/{year}/"
        "{tile_key}.tif` (anonymous read), tile keys like `01KFS_0_0`.",
        "- Band 1 `field`, band 2 `boundary`; uint8, probability = "
        "value / 255 (the files carry scale 1/255). 2.5 m, per-tile UTM "
        "CRS (`epsg` in the index).",
        f"- Enumerate tiles via the [index manifest]({INDEX_URL}) "
        f"(`year = {year}`), never by listing the bucket.",
        "- Mean field fraction across tiles in "
        f"{year}: {stats['mean_field_frac']}.",
        "- Each COG's GDAL metadata names its four source mosaic tiles "
        "(`source_items`) and the model (`unet_balanced_fp32.onnx`).", "",
        "Runnable example:", "",
        *_index_query(year), "",
    ])



def tree_readme(stats: dict[int, dict]) -> str:
    total = sum(s["n"] for s in stats.values())
    return "\n".join([
        "# FTW Global (2nd Edition) — Field & boundary probability rasters", "",
        f"Per-year collections of 2.5 m field/boundary probability COGs, "
        f"{min(stats)}–{max(stats)}: **{total:,} tiles**. {_PROJECT}", "",
        f"**[Open the interactive map]({VIEWER_URL})** to explore the "
        f"predictions, or **[open the catalog in the Portolan browser]"
        f"({browser('raster/catalog.json')})** to walk the metadata. The "
        f"files are listed on [Source Cooperative]({SOURCE_COOP}).", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## Collections", "",
        "| Year | Tiles | Size | Browse |",
        "|---|---|---|---|",
        *[f"| {y} | [{stats[y]['n']:,}]({folder(f'raster/{y}')}) "
          f"| {stats[y]['bytes'] / 1e12:.2f} TB "
          f"| [map]({viewer(y)}) · "
          f"[browser]({browser(f'raster/{y}/collection.json')}) |"
          for y in sorted(stats)], "",
    ])


def tree_agents(stats: dict[int, dict]) -> str:
    return "\n".join([
        "# AGENTS.md — FTW raster tree", "",
        "Guidance for AI agents. Every claim here is measured from the "
        "index manifest or quoted from a verified `gdalinfo`.", "",
        "- One collection per year, " + ", ".join(
            f"`{y}/collection.json`" for y in sorted(stats)) + ".",
        f"- Enumerate tiles via the [index manifest]({INDEX_URL}); "
        "read each year's AGENTS.md for band semantics.",
        "- All years share the tile grid, so per-pixel year-over-year "
        "comparison works tile by tile.", "",
    ])



def patch_local_assets(year_dir: Path, collection: dict) -> None:
    """Fill file:size/file:checksum on assets whose files exist locally."""
    for key, asset in collection["assets"].items():
        href = asset["href"]
        if "://" in href:
            continue
        local = (year_dir / href).resolve()
        if not local.is_file():
            print(f"note: {local} absent; asset '{key}' left without file:* "
                  "(generate it and re-run this script)")
            continue
        digest = hashlib.sha256()
        with local.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        asset["file:size"] = local.stat().st_size
        asset["file:checksum"] = "1220" + digest.hexdigest()


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def cmd_collections(out: Path) -> int:
    con = connect()
    stats = read_year_stats(con)
    if sorted(stats) != sorted(YEARS):
        raise SystemExit(f"index years {sorted(stats)} != expected {YEARS}")
    for year, year_stats in sorted(stats.items()):
        year_dir = out / str(year)
        collection = build_collection(year, year_stats)
        patch_local_assets(year_dir, collection)
        write_json(year_dir / "collection.json", collection)
        (year_dir / "README.md").write_text(year_readme(year, year_stats))
        (year_dir / "AGENTS.md").write_text(year_agents(year, year_stats))
        print(f"{year}: {year_stats['n']:,} tiles, "
              f"{year_stats['bytes'] / 1e12:.2f} TB")
    write_json(out / "catalog.json", build_raster_catalog(stats))
    (out / "README.md").write_text(tree_readme(stats))
    (out / "AGENTS.md").write_text(tree_agents(stats))
    print(f"OK -> {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collections = sub.add_parser(
        "collections", help="emit the committed year collections + docs"
    )
    collections.add_argument(
        "--out", type=Path, required=True,
        help="draft directory; do not point this at catalog/raster (legacy flat templates)",
    )
    args = parser.parse_args()
    if args.command == "collections":
        return cmd_collections(args.out)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
