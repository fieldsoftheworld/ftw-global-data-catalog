#!/usr/bin/env python3
"""Generate the raster tree of the beta catalog: collections, items, mirror.

Three subcommands, in the order you run them:

``collections`` — the committed metadata under ``catalog/raster/``:

- ``catalog.json`` — the raster subtree catalog (children: the 9 year
  collections).
- ``{year}/collection.json`` — one collection per year of field/boundary
  probability COGs, extents and measured numbers from
  ``index/raster.parquet``, band facts from :data:`BANDS`, and the
  bucket-side assets (``overview.tif``, its ``thumbnail.webp``, the
  ``items.parquet`` collection mirror) registered only when an HTTP probe
  finds them. Re-run it once the other producers land their objects and the
  assets appear; nothing here ever advertises an href that 404s.
- README.md / AGENTS.md for the subtree and each collection.

``headers`` — the one-time COG header pass. Tile origins are **not** derivable
from the tile key (measured: ``01KFS_0_0`` starts at (600000, 7700020),
``33UUU_0_0`` at (300000, 5900040)), so ``proj:transform`` can only come from
the files. Reads each COG's header over ``/vsicurl`` (one 64 KiB range request
is usually enough) and appends a line to a resumable JSONL sidecar. 67k tiles,
so it is resumable by design: re-running skips what the sidecar already holds.

``items`` — the ~7,466 items per year, built from the index plus that sidecar,
written to the staging tree and uploaded straight to S3. They are **not**
committed (docs/plan.md Phase 4: "items **straight to S3**"); 67k JSON files
in git would be paid for by every clone.

Published layout (one directory per item, PORTO-CORE-071, matching the
vector tree's ``zone=NN/`` precedent)::

    raster/{year}/{tile}/{tile}.tif        the COG
    raster/{year}/{tile}/{tile}.json       the item (this script)
    raster/{year}/{tile}/{tile}.thumb.png  the per-item thumbnail
    raster/{year}/overview.tif             the per-year global overview COG
    raster/{year}/thumbnail.webp           its render
    raster/{year}/items.parquet            the stac-geoparquet mirror
    raster/{year}/collection.json          committed, from `collections`

Data hrefs are derived from that layout, not from the index's ``href``
column, because the index is rewritten to the nested keys separately.

    .venv/bin/python3 tools/build_raster_items.py collections
    .venv/bin/python3 tools/build_raster_items.py headers --year 2025
    .venv/bin/python3 tools/build_raster_items.py items --year 2025 --mirror
    .venv/bin/python3 tools/build_raster_items.py items --confirm   # upload

Band facts in :data:`BANDS` are quoted from verified ``rasterio`` reads of
``raster/2025/01KFS_0_0.tif`` and three other tiles across three years
(40032×40032 @ 2.5 m, bands ``field`` and ``boundary``, uint8 with scale
1/255, no nodata, ZSTD COG, average-resampled overviews 4–64, GDAL tags
``model=unet_balanced_fp32.onnx``,
``source_bands=B02,B03,B04,B08 x Q1-Q4``). The same constant feeds the
collections' ``item_assets``, every item's ``bands``, and the README band
table, so those three cannot drift apart.

Edit this generator, never its output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish import (  # noqa: E402
    Upload,
    aws_cli,
    content_type_for,
    is_unchanged,
    load_config,
    split_s3_uri,
    upload_all,
)

ROOT = Path(__file__).resolve().parent.parent
_CONFIG = load_config()

PUBLIC_BASE = _CONFIG["public_base"].rstrip("/")
WRITE_PREFIX = _CONFIG["write_prefix"].rstrip("/")
INDEX_URL = f"{PUBLIC_BASE}/index/raster.parquet"
YEARS = tuple(range(2017, 2026))

# Where the header sidecar and the generated items live. Both are outside
# catalog/, so neither can be published by tools/publish.py; the item tree's
# keys mirror its layout under the write prefix.
SIDECAR = ROOT / "staging-data" / "checksums" / "raster_headers.jsonl"
ITEMS_DIR = ROOT / "staging-data" / "raster"

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"
PROJ_EXT = "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
RASTER_EXT = "https://stac-extensions.github.io/raster/v2.0.0/schema.json"
FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
ALT_EXT = "https://stac-extensions.github.io/alternate-assets/v1.2.0/schema.json"

MOSAICS_URL = "https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"
FTW_URL = "https://fieldsofthe.world"
DATA_BROWSER = "https://source.coop/ftw/global-data-beta"

COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
GEOTIFF_TYPE = "image/tiff; application=geotiff"
UA = "Mozilla/5.0 (ftw-global-data-catalog build_raster_items)"

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

# ── the band facts, in one place ─────────────────────────────────────────────
# Verified by reading four COGs across 2017/2021/2025 (see the module
# docstring). `raster:scale` is the files' own declared scale, so
# probability = raw * raster:scale. No nodata is declared in any file and no
# mask band exists, so no `nodata` key appears here: an invented nodata would
# make readers drop real zero-probability pixels.
GSD = 2.5
TILE_SHAPE = [40032, 40032]
MODEL = "unet_balanced_fp32.onnx"
SCALE = 1 / 255

BANDS = [
    {
        "name": "field",
        "description": "Field-interior probability: the model's probability "
                       "that the pixel lies inside an agricultural field. "
                       "probability = value × 1/255.",
        "data_type": "uint8",
        "raster:scale": SCALE,
        "raster:offset": 0,
        "raster:sampling": "area",
        "raster:spatial_resolution": GSD,
    },
    {
        "name": "boundary",
        "description": "Field-boundary probability: the model's probability "
                       "that the pixel lies on a field boundary. "
                       "probability = value × 1/255.",
        "data_type": "uint8",
        "raster:scale": SCALE,
        "raster:offset": 0,
        "raster:sampling": "area",
        "raster:spatial_resolution": GSD,
    },
]

_BANDS_PROSE = (
    f"Each COG is {TILE_SHAPE[1]:,} × {TILE_SHAPE[0]:,} pixels at {GSD} m in "
    "its tile's UTM zone, with two uint8 bands scaled by 1/255: `field` "
    "(band 1, field-interior probability) and `boundary` (band 2, "
    "field-boundary probability). No nodata value is declared, so every "
    "pixel carries a probability. ZSTD-compressed COG layout with "
    "average-resampled overviews down to 626 px. Produced by the "
    f"`{MODEL.removesuffix('.onnx')}` FTW model from 16 input bands "
    "(B02/B03/B04/B08 × quarters Q1–Q4 of the year's "
    f"[Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}), 10 m); each "
    "COG's GDAL metadata records its four source mosaic tiles "
    "(`source_items`)."
)


def band_table() -> list[str]:
    """The band facts as a markdown table, generated from BANDS.

    The table and the items' `bands` come from the same constant, so the
    documented band semantics cannot drift from the metadata.
    """
    rows = [
        "| # | Band | Type | Scale | Resolution | Meaning |",
        "|---|---|---|---|---|---|",
    ]
    for number, band in enumerate(BANDS, 1):
        rows.append(
            f"| {number} | `{band['name']}` | {band['data_type']} | "
            f"1/255 | {band['raster:spatial_resolution']} m | "
            f"{band['description']} |"
        )
    return rows


# ── layout ───────────────────────────────────────────────────────────────────

def item_dir_key(year: int, tile: str) -> str:
    """The object-key prefix of one item's own directory."""
    return f"raster/{year}/{tile}"


def cog_url(year: int, tile: str, nested: bool = True) -> str:
    """The public URL of one COG.

    ``nested`` is the published layout (``raster/{year}/{tile}/{tile}.tif``).
    The flat legacy key is still readable while the server-side copy runs, so
    the header pass can fall back to it.
    """
    if nested:
        return f"{PUBLIC_BASE}/{item_dir_key(year, tile)}/{tile}.tif"
    return f"{PUBLIC_BASE}/raster/{year}/{tile}.tif"


def cog_s3_uri(year: int, tile: str) -> str:
    """The S3 URI of one COG, derived from write_prefix in the config."""
    return f"{WRITE_PREFIX}/{item_dir_key(year, tile)}/{tile}.tif"


# ── the index ────────────────────────────────────────────────────────────────

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
               round(avg(field_frac), 4) AS mean_field_frac,
               round(max(field_frac), 4) AS max_field_frac,
               list_sort(list_distinct(list(epsg))) AS epsgs,
               min(tile_key) AS sample_tile
        FROM '{INDEX_URL}' GROUP BY year ORDER BY year
    """).fetchall()
    out = {}
    for (year, n, size, xmin, ymin, xmax, ymax, mff, xff, epsgs,
         sample) in rows:
        out[year] = {
            "n": n, "bytes": size, "bbox": [xmin, ymin, xmax, ymax],
            "mean_field_frac": mff, "max_field_frac": xff,
            "epsgs": [int(e) for e in epsgs], "sample_tile": sample,
        }
    return out


INDEX_COLS = ("year", "tile_key", "epsg", "size_bytes", "field_frac",
              "boundary_frac", "cropland_frac", "xmin", "ymin", "xmax",
              "ymax", "geom")


def read_index(con, years: tuple[int, ...] | None = None) -> list[dict]:
    """Every tile row needed to build an item.

    The ``href``/``s3_href`` columns are deliberately not read: data hrefs
    come from the published layout, which the index is rewritten to match
    separately.
    """
    where = ""
    if years:
        where = "WHERE year IN (" + ", ".join(str(int(y)) for y in years) + ")"
    rows = con.execute(f"""
        SELECT year, tile_key, epsg, size_bytes, field_frac, boundary_frac,
               cropland_frac, xmin, ymin, xmax, ymax,
               ST_AsGeoJSON(geometry) AS geom
        FROM '{INDEX_URL}' {where} ORDER BY year, tile_key
    """).fetchall()
    return [dict(zip(INDEX_COLS, r)) for r in rows]


# ── HTTP probes ──────────────────────────────────────────────────────────────

class ProbeError(RuntimeError):
    """The probe could not tell PRESENT from ABSENT."""


def probe(url: str, timeout: int = 60) -> int | None:
    """Content-Length when the object exists, None on a clean 404.

    Anything else raises: a registration gate that treats a timeout as
    "absent" silently un-publishes assets whenever the network hiccups.
    """
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise ProbeError(f"{url}: HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - network failure, not absence
        raise ProbeError(f"{url}: {exc}") from exc


def probe_many(urls: list[str], workers: int = 12) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, u): u for u in urls}
        for future in as_completed(futures):
            out[futures[future]] = future.result()
    return out


def fetch(url: str, dest: Path, timeout: int = 120) -> int:
    """Download one small object (a thumbnail) into the catalog."""
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        payload = resp.read()
    dest.write_bytes(payload)
    return len(payload)


def multihash(payload: bytes) -> str:
    """The Portolan ``file:checksum`` form: sha2-256 multihash prefix + hex."""
    return "1220" + hashlib.sha256(payload).hexdigest()


# ── collections ──────────────────────────────────────────────────────────────

def item_assets_template() -> dict[str, dict]:
    """The shape of every item's assets, from the one band constant."""
    return {
        "data": {
            "type": COG_TYPE,
            "title": "Field & boundary probabilities (COG)",
            "roles": ["data"],
            "bands": BANDS,
        },
        "thumbnail": {
            "type": "image/png",
            "title": "Field probability preview",
            "roles": ["thumbnail"],
        },
    }


def probe_urls(year: int, sample_tile: str) -> dict[str, str]:
    """What `collections` probes for one year, by report key.

    The three collection-level objects are probed because they are
    registered as assets. The three ``{sample_tile}/`` objects are probed
    because the docs describe the per-item directory and give a runnable
    ``gdalinfo`` command: one tile's directory proves the published layout is
    live, so the docs describe what is there rather than what is planned.
    """
    base = f"{PUBLIC_BASE}/raster/{year}"
    tile_dir = f"{base}/{sample_tile}"
    return {
        "overview": f"{base}/overview.tif",
        "thumbnail.webp": f"{base}/thumbnail.webp",
        "items.parquet": f"{base}/items.parquet",
        "cog": f"{tile_dir}/{sample_tile}.tif",
        "item": f"{tile_dir}/{sample_tile}.json",
        "thumb": f"{tile_dir}/{sample_tile}.thumb.png",
    }


def bucket_assets(year: int, year_dir: Path, probes: dict[str, int | None],
                  sample_tile: str, staging: Path | None = None,
                  ) -> tuple[dict[str, dict], dict]:
    """The assets that live only in the bucket, each gated on a probe.

    Returns (assets, report). Every href here is HEAD-verified before it is
    written, so a collection never advertises an object that does not exist.
    Re-running picks each one up as soon as its producer lands it.
    """
    assets: dict[str, dict] = {}
    urls = probe_urls(year, sample_tile)
    report = {
        key: ("PRESENT" if probes.get(urls[key]) is not None else "ABSENT")
        for key in ("cog", "item", "thumb")
    }
    base = f"{PUBLIC_BASE}/raster/{year}"

    size = probes.get(f"{base}/overview.tif")
    report["overview"] = "PRESENT" if size is not None else "ABSENT"
    if size is not None:
        assets["overview"] = {
            "href": "./overview.tif",
            "type": GEOTIFF_TYPE,
            "title": f"Global field-probability overview {year} (COG)",
            "description": "The whole year mosaicked and colormapped at "
                           "overview resolution, so the collection renders "
                           "at global scale.",
            "roles": ["visual", "overview", "cloud-optimized"],
            "file:size": size,
        }

    size = probes.get(f"{base}/thumbnail.webp")
    report["thumbnail.webp"] = "PRESENT" if size is not None else "ABSENT"
    if size is not None:
        local = year_dir / "thumbnail.webp"
        payload_size = fetch(f"{base}/thumbnail.webp", local)
        assets["thumbnail"] = {
            "href": "./thumbnail.webp",
            "type": "image/webp",
            "title": f"Global field-probability overview {year}",
            "roles": ["thumbnail", "overview"],
            "file:size": payload_size,
            "file:checksum": multihash(local.read_bytes()),
        }

    size = probes.get(f"{base}/items.parquet")
    report["items.parquet"] = "PRESENT" if size is not None else "ABSENT"
    if size is not None:
        mirror = {
            "href": "./items.parquet",
            "type": "application/vnd.apache.parquet",
            "title": "STAC GeoParquet mirror of the items",
            "description": "Every item's metadata in one stac-geoparquet "
                           "file, for bulk and spatial queries without "
                           "fetching thousands of item JSONs. Derived from "
                           "the items; the item JSON stays normative.",
            "roles": ["collection-mirror", "metadata"],
            "file:size": size,
        }
        local = (staging or ITEMS_DIR) / str(year) / "items.parquet"
        if local.is_file() and local.stat().st_size == size:
            mirror["file:checksum"] = multihash(local.read_bytes())
        assets["mirror"] = mirror

    return assets, report


def cog_rel_path(year: int, report: dict) -> str:
    """Where the COGs are *now*, in documentation form.

    The published layout is one directory per item, and the server-side copy
    into those keys runs separately. Until a probed tile directory answers,
    the docs name the flat key that actually resolves; a re-run switches
    them over. No sentence here describes a key that 404s.
    """
    if report.get("cog") == "PRESENT":
        return f"raster/{year}/{{tile}}/{{tile}}.tif"
    return f"raster/{year}/{{tile}}.tif"


def build_collection(year: int, stats: dict, extra: dict[str, dict],
                     report: dict) -> dict:
    tb = stats["bytes"] / 1e12
    assets: dict[str, dict] = {}
    if "thumbnail" in extra:
        assets["thumbnail"] = extra["thumbnail"]
        assets["coverage"] = {
            "href": "./thumbnail.png",
            "type": "image/png",
            "title": f"Tile coverage shaded by field fraction ({year})",
            "roles": ["overview"],
        }
    else:
        assets["thumbnail"] = {
            "href": "./thumbnail.png",
            "type": "image/png",
            "title": f"Tile coverage shaded by field fraction ({year})",
            "roles": ["thumbnail"],
        }
    for key in ("overview", "mirror"):
        if key in extra:
            assets[key] = extra[key]
    per_tile = [name for key, name in (("item", "its STAC item"),
                                       ("thumb", "a thumbnail"))
                if report.get(key) == "PRESENT"]
    browse = ""
    if per_tile:
        browse = ("\n\nEach tile's directory also holds "
                  + " and ".join(per_tile) + ".")
    if "overview" in extra:
        browse += ("\n\nThe year's `overview` asset renders the whole "
                   "collection at global scale.")
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT, PROJ_EXT, RASTER_EXT, FILE_EXT],
        "id": f"ftw-raster-{year}",
        "title": f"FTW Global — Field & Boundary Probabilities {year} (COG)",
        "description": (
            f"Field and boundary probability rasters for {year}: "
            f"{stats['n']:,} Cloud-Optimized GeoTIFFs at {GSD} m "
            f"({tb:.2f} TB), one per Sentinel-2 MGRS-based tile at "
            f"`{cog_rel_path(year, report)}`, browsable in the "
            f"[data browser]({DATA_BROWSER}). {_PROJECT}\n\n"
            f"**The rasters.** {_BANDS_PROSE}\n\n"
            f"Per-item STAC metadata is generated to the bucket next to "
            f"each COG; the [index manifest]({INDEX_URL}) lists every tile "
            "with href, size, bbox, and per-tile field/boundary/cropland "
            f"pixel fractions.{browse}"
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
        "summaries": {
            "gsd": [GSD],
            "proj:code": [f"EPSG:{code}" for code in stats["epsgs"]],
        },
        "item_assets": item_assets_template(),
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
        ],
        "assets": assets,
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
            f"{GSD} m, {min(stats)}–{max(stats)}: {total:,} tiles, "
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
            *children,
        ],
    }


# ── items ────────────────────────────────────────────────────────────────────

def item_properties(row: dict, header: dict) -> dict:
    year, tile = row["year"], row["tile_key"]
    transform = header["transform"]
    height, width = header["shape"]
    xmin, ymax = transform[2], transform[5]
    proj_bbox = [xmin, ymax + transform[4] * height,
                 xmin + transform[0] * width, ymax]
    props = {
        "title": f"{tile} — {year} field & boundary probabilities",
        "description": (
            f"Field and boundary probability raster for Sentinel-2 tile "
            f"{tile} in {year}: {width:,} × {height:,} pixels at {GSD} m in "
            f"EPSG:{header['epsg']}. {row['field_frac'] * 100:.1f}% of "
            f"pixels are field interior and "
            f"{row['boundary_frac'] * 100:.1f}% field boundary at the "
            "dataset's own thresholds. Probability = pixel value × 1/255."
        ),
        "datetime": f"{year}-01-01T00:00:00Z",
        "start_datetime": f"{year}-01-01T00:00:00Z",
        "end_datetime": f"{year}-12-31T23:59:59Z",
        "gsd": GSD,
        "proj:code": f"EPSG:{header['epsg']}",
        "proj:shape": [height, width],
        "proj:transform": transform,
        "proj:bbox": proj_bbox,
        "ftw:tile_key": tile,
        "ftw:field_frac": round(row["field_frac"], 6),
        "ftw:boundary_frac": round(row["boundary_frac"], 6),
        "ftw:cropland_frac": round(row["cropland_frac"], 6),
        "ftw:model": header.get("model") or MODEL,
    }
    if header.get("source_items"):
        props["ftw:source_items"] = header["source_items"]
    return props


def build_item(row: dict, header: dict, with_thumbnail: bool = True) -> dict:
    year, tile = row["year"], row["tile_key"]
    data_asset = {
        "href": f"./{tile}.tif",
        "type": COG_TYPE,
        "title": f"{tile} {year} — field & boundary probabilities (COG)",
        "roles": ["data"],
        "bands": BANDS,
        "file:size": row["size_bytes"],
        "alternate": {
            "s3": {
                "href": cog_s3_uri(year, tile),
                "title": "S3 URI (us-west-2, anonymous read)",
            }
        },
    }
    assets = {"data": data_asset}
    if with_thumbnail:
        assets["thumbnail"] = {
            "href": f"./{tile}.thumb.png",
            "type": "image/png",
            "title": f"{tile} {year} — field probability preview",
            "roles": ["thumbnail"],
        }
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "stac_extensions": [PROJ_EXT, RASTER_EXT, FILE_EXT, ALT_EXT],
        "id": tile,
        "geometry": json.loads(row["geom"]),
        "bbox": [row["xmin"], row["ymin"], row["xmax"], row["ymax"]],
        "properties": item_properties(row, header),
        "collection": f"ftw-raster-{year}",
        "assets": assets,
        "links": [
            {"rel": "root", "href": "../../../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (beta)"},
            {"rel": "parent", "href": "../collection.json",
             "type": "application/json"},
            {"rel": "collection", "href": "../collection.json",
             "type": "application/json"},
            {"rel": "derived_from", "href": MOSAICS_URL, "type": "text/html",
             "title": "TGE Labs Sentinel-2 quarterly cloudless mosaics "
                      "(source item ids in ftw:source_items)"},
        ],
    }


# ── the COG header pass ──────────────────────────────────────────────────────

GDAL_ENV = {
    # One 64 KiB range request usually covers a COG header, and READDIR is a
    # wasted listing of a prefix that holds 67k objects.
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "CPL_VSIL_CURL_CHUNK_SIZE": "65536",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "GDAL_HTTP_LOW_SPEED_LIMIT": "1000",
    "GDAL_HTTP_LOW_SPEED_TIME": "30",
    "GDAL_HTTP_USERAGENT": UA,
    # s3:// would make GDAL probe the EC2 metadata endpoint, which the rails
    # nodes blackhole; every read here is an https URL.
    "AWS_NO_SIGN_REQUEST": "YES",
}


def apply_gdal_env() -> None:
    for key, value in GDAL_ENV.items():
        os.environ.setdefault(key, value)


def read_header(url: str) -> dict:
    """Shape, CRS and transform from one COG's header, over the network."""
    import rasterio

    with rasterio.open(url) as ds:
        return {
            "epsg": ds.crs.to_epsg(),
            "shape": [ds.height, ds.width],
            "transform": [ds.transform.a, ds.transform.b, ds.transform.c,
                          ds.transform.d, ds.transform.e, ds.transform.f],
            "count": ds.count,
            "dtypes": list(ds.dtypes),
            "scales": list(ds.scales),
            "nodata": list(ds.nodatavals),
            "descriptions": list(ds.descriptions),
            "overviews": list(ds.overviews(1)),
            "model": (ds.tags() or {}).get("model"),
            "source_items": json.loads(
                (ds.tags() or {}).get("source_items") or "null"
            ),
        }


def header_deviations(row: dict, header: dict) -> list[str]:
    """How one header differs from the facts the catalog documents."""
    out = []
    if header["shape"] != TILE_SHAPE:
        out.append(f"shape {header['shape']} != {TILE_SHAPE}")
    if header["count"] != len(BANDS):
        out.append(f"{header['count']} band(s) != {len(BANDS)}")
    if set(header["dtypes"]) != {"uint8"}:
        out.append(f"dtypes {header['dtypes']} != uint8")
    if [round(s, 12) for s in header["scales"]] != [round(SCALE, 12)] * 2:
        out.append(f"scales {header['scales']} != 1/255")
    if any(n is not None for n in header["nodata"]):
        out.append(f"nodata {header['nodata']} declared")
    if header["descriptions"] != [b["name"] for b in BANDS]:
        out.append(f"band names {header['descriptions']}")
    if header["epsg"] != row["epsg"]:
        out.append(f"epsg {header['epsg']} != index {row['epsg']}")
    if abs(header["transform"][0] - GSD) > 1e-9:
        out.append(f"pixel size {header['transform'][0]} != {GSD}")
    return out


def load_sidecar(path: Path) -> dict[tuple[int, str], dict]:
    """The resumable sidecar: (year, tile) -> header. Bad lines are dropped.

    JSONL, not one rewritten JSON object: 67k entries rewritten after every
    completion is quadratic, and an append survives a kill mid-run.
    """
    out: dict[tuple[int, str], dict] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line from a killed run
        if "year" in entry and "tile_key" in entry:
            out[(int(entry["year"]), entry["tile_key"])] = entry
    return out


def cmd_headers(args) -> int:
    apply_gdal_env()
    con = connect()
    rows = read_index(con, tuple(args.year) if args.year else None)
    done = load_sidecar(args.sidecar)
    todo = [r for r in rows if (r["year"], r["tile_key"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(rows)} tile(s) in scope, {len(done)} already in "
          f"{args.sidecar.name}, {len(todo)} to read")
    if not todo:
        return 0

    args.sidecar.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    deviations: list[str] = []
    failed: list[str] = []

    def one(row: dict) -> dict:
        year, tile = row["year"], row["tile_key"]
        layouts = ([True, False] if args.layout == "auto"
                   else [args.layout == "nested"])
        last: Exception | None = None
        for nested in layouts:
            try:
                header = read_header(cog_url(year, tile, nested))
            except Exception as exc:  # noqa: BLE001 - try the other layout
                last = exc
                continue
            header |= {"year": year, "tile_key": tile,
                       "layout": "nested" if nested else "flat"}
            return header
        raise last if last else AssertionError("unreachable")

    with args.sidecar.open("a") as handle, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, r): r for r in todo}
        for i, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            label = f"{row['year']}/{row['tile_key']}"
            try:
                header = future.result()
            except Exception as exc:  # noqa: BLE001 - report, keep going
                failed.append(f"{label}: {exc}")
                print(f"  FAILED {label}: {exc}", file=sys.stderr, flush=True)
                continue
            bad = header_deviations(row, header)
            if bad:
                header["deviations"] = bad
                deviations.append(f"{label}: {'; '.join(bad)}")
            with lock:
                handle.write(json.dumps(header, sort_keys=True) + "\n")
                handle.flush()
            if i % 100 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} read", flush=True)

    if deviations:
        print(f"\n{len(deviations)} tile(s) deviate from the documented band "
              "facts:", file=sys.stderr)
        for line in deviations[:20]:
            print(f"  {line}", file=sys.stderr)
    if failed:
        print(f"\n{len(failed)} tile(s) failed; re-run to retry them",
              file=sys.stderr)
        return 1
    print(f"\nOK: {len(todo)} header(s) -> {args.sidecar}")
    return 0


# ── the mirror ───────────────────────────────────────────────────────────────

def write_mirror_scaffold(out: Path, years: list[int],
                          tiles: dict[int, list[str]]) -> None:
    """The minimum tree ``portolan stac-geoparquet`` needs to find the items.

    Staging only: these files are never uploaded (the uploader below takes
    only ``{tile}/{tile}.json`` keys, and tools/upload_data.py admits only
    data suffixes), and they exist so the mirror is built from the same item
    JSON that is published.
    """
    (out / ".portolan").mkdir(parents=True, exist_ok=True)
    (out / ".portolan" / "config.yaml").write_text("# Portolan configuration\n")
    for year in sorted(years):
        source = ROOT / "catalog" / "raster" / str(year) / "collection.json"
        collection = json.loads(source.read_text())
        collection["links"] = [
            {"rel": "root", "href": "../catalog.json",
             "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json"},
            *[{"rel": "item", "href": f"./{t}/{t}.json",
               "type": "application/geo+json"} for t in tiles[year]],
        ]
        collection["assets"] = {}
        write_json(out / str(year) / "collection.json", collection)
    # Every year directory that has a scaffold, not only the ones built this
    # run, so the staging root stays a consistent catalog across partial runs.
    present = sorted(
        int(p.parent.name) for p in out.glob("*/collection.json")
        if p.parent.name.isdigit()
    )
    write_json(out / "catalog.json", {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT],
        "id": "raster-items-staging",
        "title": "Staging tree for the raster item mirror",
        "description": "Not published. Exists so `portolan stac-geoparquet` "
                       "can read the generated items.",
        "links": [
            {"rel": "root", "href": "./catalog.json",
             "type": "application/json"},
            *[{"rel": "child", "href": f"./{y}/collection.json",
               "type": "application/json"} for y in present],
        ],
    })


def build_mirror(out: Path, years: list[int]) -> int:
    """Run ``portolan stac-geoparquet`` over the staging tree, per year.

    ``-c`` names the collection by its **directory**, not its STAC id
    (measured: ``-c ftw-raster-2025`` reports "Collection not found",
    ``-c 2025`` works), so the year directory is what gets passed.
    """
    import subprocess

    portolan = ROOT / ".venv" / "bin" / "portolan"
    if not portolan.is_file():
        import shutil
        found = shutil.which("portolan")
        if not found:
            print("note: portolan not found; skipping the items.parquet "
                  "mirror. Install it (.venv) and re-run with --mirror.")
            return 1
        portolan = Path(found)
    failures = 0
    for year in sorted(years):
        proc = subprocess.run(
            [str(portolan), "stac-geoparquet", "--catalog", str(out),
             "-c", str(year)],
            capture_output=True, text=True,
        )
        parquet = out / str(year) / "items.parquet"
        if proc.returncode != 0 or not parquet.is_file():
            failures += 1
            print(f"  mirror {year} FAILED: "
                  f"{(proc.stderr or proc.stdout).strip()[:400]}",
                  file=sys.stderr)
            continue
        print(f"  mirror {year}: {parquet.stat().st_size / 2**20:.1f} MiB "
              f"-> {parquet}")
    return failures


# ── the item uploader ────────────────────────────────────────────────────────

def item_uploads(out: Path, prefix: str) -> list[Upload]:
    """Every generated item JSON, with the object key it publishes to.

    One rule, and it is narrow on purpose: a file at
    ``{out}/{year}/{tile}/{tile}.json`` publishes to
    ``{prefix}/raster/{year}/{tile}/{tile}.json``. The staging scaffold
    (``catalog.json``, ``collection.json``) and the mirror parquet are not
    item JSON and do not match, so this uploader cannot touch them —
    ``collection.json`` is published from ``catalog/`` by tools/publish.py,
    and ``items.parquet`` by tools/upload_data.py.
    """
    uploads = []
    for path in sorted(out.glob("*/*/*.json")):
        tile_dir = path.parent
        if path.stem != tile_dir.name:
            continue
        year = tile_dir.parent.name
        if not year.isdigit():
            continue
        key = f"raster/{year}/{tile_dir.name}/{path.name}"
        uploads.append(Upload(
            path, f"{prefix}/{key}" if prefix else key, content_type_for(path)
        ))
    return uploads


def item_remote_index(years: list[int], config: dict[str, str],
                      ) -> dict[str, tuple[int, str]]:
    """Size and ETag for the item objects, one recursive listing per year.

    tools/publish.py lists each catalog directory *non-recursively* because a
    recursive listing of the write prefix would walk 67k COGs. That reasoning
    inverts here. With the nested layout every item sits in its own
    directory, so a non-recursive listing would mean one request per tile —
    67k of them. A recursive listing of `raster/{year}/` returns the three
    objects per tile (COG, item, thumbnail), ~22k keys, in a couple of dozen
    paginated calls. Nine listings instead of 67,197.
    """
    import subprocess

    aws = aws_cli()
    if aws is None:
        print("note: aws CLI not found; treating every item as changed")
        return {}
    bucket, prefix = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    index: dict[str, tuple[int, str]] = {}

    def one(year: int) -> list:
        head = f"{prefix}/raster/{year}/" if prefix else f"raster/{year}/"
        return json.loads(subprocess.run(
            [aws, "s3api", "list-objects-v2", "--bucket", bucket,
             "--prefix", head, "--region", region, "--output", "json",
             "--query", "Contents[?ends_with(Key, `.json`)].[Key,Size,ETag]"],
            check=True, capture_output=True, text=True,
        ).stdout or "[]") or []

    with ThreadPoolExecutor(max_workers=len(years) or 1) as pool:
        futures = {pool.submit(one, y): y for y in years}
        for future in as_completed(futures):
            try:
                rows = future.result()
            except (subprocess.CalledProcessError, OSError) as exc:
                detail = (getattr(exc, "stderr", "") or str(exc)).strip()
                print(f"note: could not list raster/{futures[future]}/ "
                      f"({detail}); treating every item as changed")
                return {}
            for key, size, etag in rows:
                index[key] = (int(size), etag.strip('"'))
    return index


def upload_items(out: Path, confirm: bool, force: bool) -> int:
    config = load_config()
    bucket, prefix = split_s3_uri(config["write_prefix"])
    region = config.get("region", "us-west-2")
    uploads = item_uploads(out, prefix)
    if not uploads:
        print(f"no generated items under {out}")
        return 1
    years = sorted({int(p.name) for p in out.iterdir()
                    if p.is_dir() and p.name.isdigit()})
    index = {} if force else item_remote_index(years, config)
    changed = [u for u in uploads if force or not is_unchanged(u, index)]
    print(f"items:  {len(uploads)} generated, {len(changed)} to upload")
    print(f"target: s3://{bucket}/{prefix}/raster/{{year}}/{{tile}}/")
    print("this never deletes")
    if not confirm:
        for upload in changed[:10]:
            print(f"  would upload  {upload.key}  ({upload.content_type})")
        if len(changed) > 10:
            print(f"  ... and {len(changed) - 10} more")
        print("\ndry run. re-run with --confirm to upload.")
        return 0
    if not changed:
        print("nothing to upload")
        return 0
    aws = aws_cli()
    if aws is None:
        sys.exit("aws CLI is required to upload and was not found on PATH")
    failed = upload_all(changed, bucket, region, aws)
    if failed:
        print(f"\n{len(failed)} of {len(changed)} item(s) failed",
              file=sys.stderr)
        return 1
    print(f"\nuploaded {len(changed)} item(s)")
    return 0


def cmd_items(args) -> int:
    con = connect()
    years = tuple(args.year) if args.year else None
    rows = read_index(con, years)
    sidecar = load_sidecar(args.sidecar)
    print(f"{len(rows)} tile(s) in scope, {len(sidecar)} header(s) in "
          f"{args.sidecar.name}")

    written: dict[int, list[str]] = {}
    missing = 0
    deviating = 0
    for row in rows:
        key = (row["year"], row["tile_key"])
        header = sidecar.get(key)
        if header is None:
            missing += 1
            continue
        if header.get("deviations") and not args.allow_deviations:
            deviating += 1
            continue
        tile = row["tile_key"]
        write_json(args.out / str(row["year"]) / tile / f"{tile}.json",
                   build_item(row, header, not args.no_thumbnails))
        written.setdefault(row["year"], []).append(tile)

    for year in sorted(written):
        print(f"{year}: {len(written[year]):,} item(s) -> "
              f"{args.out / str(year)}")
    if missing:
        print(f"note: {missing:,} tile(s) have no header yet; run the "
              "`headers` subcommand (it is resumable) and re-run this.")
    if deviating:
        print(f"note: {deviating:,} tile(s) deviate from the documented band "
              "facts and were skipped; inspect the sidecar's `deviations` "
              "field, or pass --allow-deviations.", file=sys.stderr)

    if written and args.mirror:
        write_mirror_scaffold(args.out, list(written), written)
        build_mirror(args.out, list(written))

    if args.confirm or args.dry_run_upload:
        return upload_items(args.out, args.confirm, args.force)
    if not written:
        return 1
    print("\nitems are not committed (docs/plan.md Phase 4). Upload them "
          "with: build_raster_items.py items --confirm")
    return 0


# ── documentation ────────────────────────────────────────────────────────────

def _tile_query(year: int) -> list[str]:
    return [
        "```python",
        "import duckdb",
        "con = duckdb.connect()",
        'con.execute("INSTALL httpfs; LOAD httpfs;")',
        f'idx = "{INDEX_URL}"',
        "con.sql(f\"\"\"",
        "    SELECT tile_key, epsg, round(field_frac, 3) AS field_frac",
        f"    FROM read_parquet('{{idx}}') WHERE year = {year}",
        "    ORDER BY field_frac DESC LIMIT 5",
        "\"\"\").show()",
        "```",
    ]


def _collection_query(year: int) -> list[str]:
    """One query that summarises the whole collection from the index."""
    return [
        "```python",
        "import duckdb",
        "con = duckdb.connect()",
        'con.execute("INSTALL httpfs; LOAD httpfs;")',
        f'idx = "{INDEX_URL}"',
        "con.sql(f\"\"\"",
        "    SELECT count(*) AS tiles,",
        "           count(DISTINCT epsg) AS utm_crs,",
        "           round(sum(size_bytes) / 1e12, 2) AS tb,",
        "           round(avg(field_frac), 4) AS mean_field_frac,",
        "           round(max(field_frac), 4) AS max_field_frac,",
        "           -- 1e10 m2 == 1 Mha; assumes tiles do not overlap",
        f"           round(sum(field_frac) * {TILE_SHAPE[0]} * {TILE_SHAPE[1]}"
        f" * {GSD ** 2} / 1e10, 1)",
        "               AS field_mha_approx",
        f"    FROM read_parquet('{{idx}}') WHERE year = {year}",
        "\"\"\").show()",
        "```",
    ]


def layout_block(year: int, report: dict) -> list[str]:
    """The per-item directory, showing only the files that are there."""
    sample = report.get("sample_tile", "{tile}")
    rows = [(f"raster/{year}/{{tile}}/{{tile}}.tif", "the COG", "cog"),
            (f"raster/{year}/{{tile}}/{{tile}}.json", "its STAC item",
             "item"),
            (f"raster/{year}/{{tile}}/{{tile}}.thumb.png", "its thumbnail",
             "thumb")]
    live = [(path, note) for path, note, key in rows
            if report.get(key) == "PRESENT"]
    if not live:
        return [
            "Each tile will get its own directory "
            f"(`raster/{year}/{{tile}}/`) holding the COG, its STAC item and "
            "its thumbnail. The copy into those keys is still running, so "
            f"the COGs currently answer at `raster/{year}/{{tile}}.tif` "
            f"(for example `{sample}.tif`).", "",
        ]
    width = max(len(path) for path, _ in live)
    return [
        "Each tile has its own directory, so the COG and its metadata sit "
        "together:", "",
        "```",
        *[f"{path:<{width}}  {note}" for path, note in live],
        "```", "",
    ]


def year_readme(year: int, stats: dict, extra: dict[str, dict],
                report: dict) -> str:
    tb = stats["bytes"] / 1e12
    sample = stats["sample_tile"]
    nested = report.get("cog") == "PRESENT"
    read_url = (f"{PUBLIC_BASE}/raster/{year}/{sample}/{sample}.tif" if nested
                else f"{PUBLIC_BASE}/raster/{year}/{sample}.tif")
    lines = [
        f"# FTW Global — Field & Boundary Probabilities {year} (COG)", "",
        f"Field and boundary probability rasters for {year}: "
        f"**{stats['n']:,} Cloud-Optimized GeoTIFFs** at {GSD} m "
        f"({tb:.2f} TB), one per Sentinel-2 MGRS-based tile. {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}).", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## The rasters", "",
        _BANDS_PROSE, "",
        *band_table(), "",
        *layout_block(year, report | {"sample_tile": sample}),
        "## Find tiles", "",
        f"The [index manifest]({INDEX_URL}) lists every tile with href, "
        "size, bbox, `epsg`, and per-tile `field_frac`/`boundary_frac`/"
        f"`cropland_frac` pixel fractions. The five field-densest tiles of "
        f"{year}:", "",
        *_tile_query(year), "",
        f"Summarising the whole {year} collection from the same manifest "
        "(no raster reads):", "",
        *_collection_query(year), "",
        "## Read a tile", "",
        "```bash",
        f"gdalinfo /vsicurl/{read_url}",
        "```", "",
        "Any COG reader works over HTTP range requests; the overviews make "
        "low-zoom reads cheap.", "",
    ]
    if "overview" in extra or "mirror" in extra:
        lines += ["## Browse it", ""]
        if "overview" in extra:
            lines += [
                f"One [global overview COG]({PUBLIC_BASE}/raster/{year}/"
                f"overview.tif) renders the whole year at global scale "
                "(the collection's `overview` asset)."
                + (" Every tile's own thumbnail sits beside its COG."
                   if report.get("thumb") == "PRESENT" else ""), "",
            ]
        if "mirror" in extra:
            lines += [
                f"The [items.parquet mirror]({PUBLIC_BASE}/raster/{year}/"
                "items.parquet) holds every item's metadata in one "
                "stac-geoparquet file, so a spatial lookup over "
                f"{stats['n']:,} tiles is one query rather than "
                f"{stats['n']:,} HTTP requests:", "",
                "```python",
                "import duckdb",
                "con = duckdb.connect()",
                'con.execute("INSTALL spatial; LOAD spatial; '
                'INSTALL httpfs; LOAD httpfs;")',
                f'url = "{PUBLIC_BASE}/raster/{year}/items.parquet"',
                "con.sql(f\"\"\"",
                "    SELECT id, assets['data']['href'] AS cog",
                "    FROM read_parquet('{url}')",
                "    WHERE ST_Intersects(geometry,",
                "          ST_Point(12.5, 55.7))",
                "\"\"\").show()",
                "```", "",
            ]
    return "\n".join(lines)


def year_agents(year: int, stats: dict, extra: dict[str, dict],
                report: dict) -> str:
    sample = stats["sample_tile"]
    beside = [name for key, name in (("item", "`{tile}.json`, its STAC item"),
                                     ("thumb", "`{tile}.thumb.png`"))
              if report.get(key) == "PRESENT"]
    lines = [
        f"# AGENTS.md — FTW probability rasters {year}", "",
        "Guidance for AI agents. Every claim here is quoted from a verified "
        "COG header or measured from the index manifest.", "",
        "Related guides: the [raster tree](../AGENTS.md), the "
        "[catalog root](../../AGENTS.md), and this collection's "
        "[README](./README.md).", "",
        f"- {stats['n']:,} COGs at `{PUBLIC_BASE}/"
        f"{cog_rel_path(year, report)}` (anonymous read), tile keys like "
        f"`{sample}`."
        + (" Each tile's directory also holds "
           + " and ".join(beside) + "." if beside else ""),
        "- Band 1 `field`, band 2 `boundary`; uint8, probability = "
        f"value / 255 (the files carry scale 1/255). {GSD} m, per-tile UTM "
        f"CRS ({len(stats['epsgs'])} distinct EPSG codes this year; `epsg` "
        "in the index, `proj:code` on each item). No nodata is declared.",
        f"- Enumerate tiles via the [index manifest]({INDEX_URL}) "
        f"(`year = {year}`), never by listing the bucket. There are no "
        "`rel: item` links on the collection: with thousands of items per "
        "year the index manifest"
        + (" and `items.parquet`"
           if report.get("items.parquet") == "PRESENT" else "")
        + " carry the enumeration.",
        f"- Measured across {year}: mean field fraction "
        f"{stats['mean_field_frac']}, max {stats['max_field_frac']}.",
        "- Each COG's GDAL metadata names its four source mosaic tiles "
        f"(`source_items`, mirrored into each item's `ftw:source_items`) "
        f"and the model (`{MODEL}`).",
        "- Tile origins are not derivable from the tile key (measured: "
        "`01KFS_0_0` starts at (600000, 7700020), `33UUU_0_0` at "
        "(300000, 5900040)). Read `proj:transform` from the item, or the "
        "COG header.", "",
    ]
    if "mirror" in extra:
        lines += [
            f"- `{PUBLIC_BASE}/raster/{year}/items.parquet` mirrors every "
            "item for bulk and spatial queries.", "",
        ]
    lines += [
        "Runnable example — the five field-densest tiles:", "",
        *_tile_query(year), "",
        "Whole-collection summary from the index:", "",
        *_collection_query(year), "",
    ]
    return "\n".join(lines)


def tree_readme(stats: dict[int, dict]) -> str:
    total = sum(s["n"] for s in stats.values())
    return "\n".join([
        "# FTW Global (beta) — Field & boundary probability rasters", "",
        f"Per-year collections of {GSD} m field/boundary probability COGs, "
        f"{min(stats)}–{max(stats)}: **{total:,} tiles**. {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}).", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## Collections", "",
        *[f"- [{y}](./{y}/collection.json) — {stats[y]['n']:,} tiles, "
          f"{stats[y]['bytes'] / 1e12:.2f} TB" for y in sorted(stats)], "",
        "## The rasters", "",
        _BANDS_PROSE, "",
        *band_table(), "",
        "All years share the tile grid, so a tile key names the same ground "
        "in every year and per-pixel year-over-year comparison works tile "
        "by tile.", "",
    ])


def tree_agents(stats: dict[int, dict], reports: dict[int, dict]) -> str:
    def anywhere(key: str) -> bool:
        return any(r.get(key) == "PRESENT" for r in reports.values())

    beside = ["`collection.json`"]
    if anywhere("items.parquet"):
        beside.append("`items.parquet`")
    if anywhere("overview"):
        beside.append("the year's global `overview.tif`")
    in_tile = ["`{tile}.tif` (the COG)"] if anywhere("cog") else []
    if anywhere("item"):
        in_tile.append("`{tile}.json` (its STAC item)")
    if anywhere("thumb"):
        in_tile.append("`{tile}.thumb.png`")
    enumerate_note = (
        f"- Enumerate tiles via the [index manifest]({INDEX_URL})"
        + (" or a year's `items.parquet`" if anywhere("items.parquet") else "")
        + ", never by listing the bucket; read each year's AGENTS.md for "
          "band semantics."
    )
    return "\n".join([
        "# AGENTS.md — FTW raster tree", "",
        "Guidance for AI agents. Every claim here is measured from the "
        "index manifest or quoted from a verified COG header.", "",
        "Related guides: the [catalog root](../AGENTS.md), the sibling "
        "[vector tree](../vector/AGENTS.md), and each year's own AGENTS.md "
        "(" + ", ".join(f"[{y}](./{y}/AGENTS.md)" for y in sorted(stats))
        + ").", "",
        "- One collection per year, " + ", ".join(
            f"`{y}/collection.json`" for y in sorted(stats)) + ".",
        ("- Layout: `raster/{year}/{tile}/` holds " + ", ".join(in_tile)
         + "; " if in_tile
         else "- Layout: the COGs answer at `raster/{year}/{tile}.tif` while "
              "the copy into one directory per item runs; ")
        + "`raster/{year}/` holds " + ", ".join(beside) + ".",
        enumerate_note,
        "- Band 1 `field`, band 2 `boundary`; uint8, probability = "
        "value / 255. No nodata is declared.",
        "- All years share the tile grid, so per-pixel year-over-year "
        "comparison works tile by tile.", "",
        "## Changing this tree", "",
        "Every file here — collections, items, README.md, AGENTS.md — is "
        "generated by `tools/build_raster_items.py`. Edit the generator and "
        "re-run it; an edit to the output is overwritten by the next run "
        "and leaves no trace of why it was made.", "",
    ])


def patch_local_assets(year_dir: Path, collection: dict) -> None:
    """Fill file:size/file:checksum on assets whose files exist locally."""
    for key, asset in collection["assets"].items():
        href = asset["href"]
        if "://" in href or "*" in href:
            continue
        local = (year_dir / href).resolve()
        if not local.is_file():
            if not (asset.get("file:size") and asset.get("file:checksum")):
                print(f"note: {local} absent; asset '{key}' left without "
                      "file:* (generate it and re-run this script)")
            continue
        payload = local.read_bytes()
        asset["file:size"] = len(payload)
        asset["file:checksum"] = multihash(payload)


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


STALE = ("llms.txt",)


def drop_stale(directory: Path) -> None:
    """Remove outputs this generator no longer emits."""
    for name in STALE:
        path = directory / name
        if path.is_file():
            path.unlink()
            print(f"removed {path.relative_to(ROOT)}")


def cmd_collections(args) -> int:
    con = connect()
    stats = read_year_stats(con)
    if sorted(stats) != sorted(YEARS):
        raise SystemExit(f"index years {sorted(stats)} != expected {YEARS}")

    urls = [url for year in sorted(stats)
            for url in probe_urls(year, stats[year]["sample_tile"]).values()]
    if args.no_probe:
        print("note: --no-probe, so no bucket-side asset is registered and "
              "the docs describe only the keys that already resolved")
        probes: dict[str, int | None] = {}
    else:
        print(f"probing {len(urls)} bucket-side object(s) ...")
        probes = probe_many(urls)

    reports: dict[int, dict] = {}
    for year, year_stats in sorted(stats.items()):
        year_dir = args.out / str(year)
        extra, report = bucket_assets(year, year_dir, probes,
                                      year_stats["sample_tile"], args.staging)
        reports[year] = report
        collection = build_collection(year, year_stats, extra, report)
        patch_local_assets(year_dir, collection)
        write_json(year_dir / "collection.json", collection)
        (year_dir / "README.md").write_text(
            year_readme(year, year_stats, extra, report))
        (year_dir / "AGENTS.md").write_text(
            year_agents(year, year_stats, extra, report))
        drop_stale(year_dir)
        state = " ".join(f"{k}={v}" for k, v in report.items())
        print(f"{year}: {year_stats['n']:,} tiles, "
              f"{year_stats['bytes'] / 1e12:.2f} TB  [{state}]")

    write_json(args.out / "catalog.json", build_raster_catalog(stats))
    (args.out / "README.md").write_text(tree_readme(stats))
    (args.out / "AGENTS.md").write_text(tree_agents(stats, reports))
    drop_stale(args.out)
    print(f"OK -> {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collections = sub.add_parser(
        "collections", help="emit the committed year collections + docs"
    )
    collections.add_argument(
        "--out", type=Path, default=ROOT / "catalog" / "raster")
    collections.add_argument(
        "--staging", type=Path, default=ITEMS_DIR,
        help="generated item tree, read for the mirror's checksum")
    collections.add_argument(
        "--no-probe", action="store_true",
        help="skip the HTTP probe; registers no bucket-side asset")

    headers = sub.add_parser(
        "headers", help="read every COG's header into the resumable sidecar"
    )
    headers.add_argument("--year", type=int, action="append",
                         help="limit to this year (repeatable)")
    headers.add_argument("--limit", type=int,
                         help="read at most this many headers this run")
    headers.add_argument("--workers", type=int, default=16)
    headers.add_argument("--sidecar", type=Path, default=SIDECAR)
    headers.add_argument("--layout", choices=("auto", "nested", "flat"),
                         default="auto",
                         help="which COG key to read (auto tries the "
                              "published nested key, then the flat legacy "
                              "key still live during the copy)")

    items = sub.add_parser(
        "items", help="build the per-tile items from the index + sidecar"
    )
    items.add_argument("--year", type=int, action="append")
    items.add_argument("--sidecar", type=Path, default=SIDECAR)
    items.add_argument("--out", type=Path, default=ITEMS_DIR)
    items.add_argument("--mirror", action="store_true",
                       help="also build each year's items.parquet")
    items.add_argument("--no-thumbnails", action="store_true",
                       help="omit the per-item thumbnail asset")
    items.add_argument("--allow-deviations", action="store_true",
                       help="build items for tiles whose header deviates "
                            "from the documented band facts")
    items.add_argument("--dry-run-upload", action="store_true",
                       help="report what an upload would change")
    items.add_argument("--confirm", action="store_true",
                       help="upload the items to the bucket")
    items.add_argument("--force", action="store_true",
                       help="re-upload every item; skip the remote listing")

    args = parser.parse_args()
    if args.command == "collections":
        return cmd_collections(args)
    if args.command == "headers":
        return cmd_headers(args)
    if args.command == "items":
        return cmd_items(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
