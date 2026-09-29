#!/usr/bin/env python3
"""Generate the raster tree of the beta catalog.

``collections`` (this phase) emits, under ``catalog/raster/``:

- ``catalog.json`` — the raster subtree catalog (children: the 9 year
  collections).
- ``{year}/collection.json`` — one collection per year of field/boundary
  probability COGs, extents and measured numbers from
  ``index/raster.parquet``.
- README.md / AGENTS.md / llms.txt for the subtree and each collection.

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

    .venv/bin/python3 tools/build_raster_items.py collections
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PUBLIC_BASE = "https://data.source.coop/ftw/global-data-beta"
INDEX_URL = f"{PUBLIC_BASE}/index/raster.parquet"
YEARS = tuple(range(2017, 2026))

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"

MOSAICS_URL = "https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"
FTW_URL = "https://fieldsofthe.world"
DATA_BROWSER = "https://source.coop/ftw/global-data-beta"

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
            f"`raster/{year}/{{tile_key}}.tif`, browsable in the "
            f"[data browser]({DATA_BROWSER}). {_PROJECT}\n\n"
            f"**The rasters.** {_BANDS}\n\n"
            f"Per-item STAC metadata is generated to the bucket next to "
            f"each COG; the [index manifest]({INDEX_URL}) lists every tile "
            "with href, size, bbox, and per-tile field/boundary/cropland "
            "pixel fractions."
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
             "title": "Fields of the World — Global Data (beta)"},
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
            {"rel": "llms", "href": "./llms.txt", "type": "text/markdown",
             "title": "Agent/LLM usage guide"},
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
        "title": "FTW Global (beta) — Field & boundary probability rasters",
        "description": (
            f"Per-year collections of field/boundary probability COGs at "
            f"2.5 m, {min(stats)}–{max(stats)}: {total:,} tiles, "
            f"{tb:.1f} TB, browsable in the "
            f"[data browser]({DATA_BROWSER}). {_PROJECT}"
        ),
        "links": [
            {"rel": "root", "href": "../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (beta)"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Raster tree README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Raster tree agent guide"},
            {"rel": "llms", "href": "./llms.txt", "type": "text/markdown",
             "title": "Agent/LLM usage guide"},
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
    return "\n".join([
        f"# FTW Global — Field & Boundary Probabilities {year} (COG)", "",
        f"Field and boundary probability rasters for {year}: "
        f"**{stats['n']:,} Cloud-Optimized GeoTIFFs** at 2.5 m "
        f"({tb:.2f} TB), one per Sentinel-2 MGRS-based tile. {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}).", "",
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


def year_llms(year: int, stats: dict) -> str:
    return "\n".join([
        f"# FTW Global (beta) — probability rasters {year}", "",
        f"> {stats['n']:,} two-band (field, boundary) uint8 probability "
        f"COGs at 2.5 m for {year}. CC-BY-4.0.", "",
        f"Data: `{PUBLIC_BASE}/raster/{year}/{{tile_key}}.tif`",
        f"Index: {INDEX_URL}",
        f"Collection: {PUBLIC_BASE}/raster/{year}/collection.json", "",
        "Probability = pixel value / 255. See AGENTS.md beside this file.",
        "",
    ])


def tree_readme(stats: dict[int, dict]) -> str:
    total = sum(s["n"] for s in stats.values())
    return "\n".join([
        "# FTW Global (beta) — Field & boundary probability rasters", "",
        f"Per-year collections of 2.5 m field/boundary probability COGs, "
        f"{min(stats)}–{max(stats)}: **{total:,} tiles**. {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}).", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## Collections", "",
        *[f"- [{y}](./{y}/collection.json) — {stats[y]['n']:,} tiles, "
          f"{stats[y]['bytes'] / 1e12:.2f} TB" for y in sorted(stats)], "",
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


def tree_llms(stats: dict[int, dict]) -> str:
    total = sum(s["n"] for s in stats.values())
    return "\n".join([
        "# FTW Global (beta) — raster tree", "",
        f"> {total:,} field/boundary probability COGs across "
        f"{len(stats)} years ({min(stats)}–{max(stats)}). CC-BY-4.0.", "",
        *[f"- {y}: {PUBLIC_BASE}/raster/{y}/collection.json"
          for y in sorted(stats)], "",
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
        (year_dir / "llms.txt").write_text(year_llms(year, year_stats))
        print(f"{year}: {year_stats['n']:,} tiles, "
              f"{year_stats['bytes'] / 1e12:.2f} TB")
    write_json(out / "catalog.json", build_raster_catalog(stats))
    (out / "README.md").write_text(tree_readme(stats))
    (out / "AGENTS.md").write_text(tree_agents(stats))
    (out / "llms.txt").write_text(tree_llms(stats))
    print(f"OK -> {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collections = sub.add_parser(
        "collections", help="emit the committed year collections + docs"
    )
    collections.add_argument(
        "--out", type=Path, default=ROOT / "catalog" / "raster",
    )
    args = parser.parse_args()
    if args.command == "collections":
        return cmd_collections(args.out)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
