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
**committed** under ``catalog/raster/``, together with the zone and GZD
catalogs that group them. Committing them is what makes the items reachable
by ``rel`` links: rashid derives containment from directory nesting and only
sees item JSON that is in the tree, so a collection whose items live only in
the bucket publishes no item connectivity at all (PTL-COL-005, and
PORTO-CORE-032 behind it — docs/conformance.md has the measurements).

Published layout (user ruling 2026-10-02; one hierarchy for data and
metadata, hive-separated like the vector tree's ``zone=NN/``)::

    raster/{year}/collection.json                        committed
    raster/{year}/zone={ZZ}/catalog.json                 committed, 54/year
    raster/{year}/zone={ZZ}/gzd={GZD}/catalog.json       committed, 356/year
    raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif        the COG
    raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.json       the item
    raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.thumb.png  its thumbnail
    raster/{year}/overview.tif             the per-year global overview COG
    raster/{year}/thumbnail.webp           its render
    raster/{year}/items.parquet            the stac-geoparquet mirror

``{ZZ}`` is the tile key's two leading digits (the UTM zone) and ``{GZD}``
those digits plus the latitude-band letter — ``01KFS_0_0`` groups under
``zone=01/gzd=01K/``. Every catalog directory carries README.md and AGENTS.md
because PTL-FIL-001/002/003 bind plain catalogs too, not only collections
(measured). The data sits beside its item, so every item asset href is
``./{tile}.tif`` / ``./{tile}.thumb.png``, with no reach-back.

Data hrefs are derived from that layout, not from the index's ``href``
column, because the index is rewritten to the grouped keys separately.

    .venv/bin/python3 tools/build_raster_items.py headers --year 2025
    .venv/bin/python3 tools/build_raster_items.py items --year 2025
    .venv/bin/python3 tools/build_raster_items.py collections
    .venv/bin/python3 tools/build_raster_items.py mirror --year 2025
    .venv/bin/python3 tools/build_raster_items.py items --confirm   # upload

That order is not arbitrary. ``items`` comes before ``collections`` because a
year collection links the zone catalogs that are on disk and nothing else,
and ``mirror`` comes after ``collections`` because ``portolan
stac-geoparquet`` finds the nested items by following those ``child`` links.
Re-run ``collections`` once the new mirror is uploaded, to pick up its
``file:size``/``file:checksum``.

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

# The header sidecar stays outside catalog/ (it is build state, not metadata).
# The items do not: they are committed, so they live in the published tree and
# tools/publish.py syncs them like every other metadata file.
SIDECAR = ROOT / "staging-data" / "checksums" / "raster_headers.jsonl"
ITEMS_DIR = ROOT / "catalog" / "raster"
# Where the per-year stac-geoparquet mirror lands. It is a data file, so it
# never sits in catalog/ — tools/upload_data.py publishes it from here.
MIRROR_DIR = ROOT / "staging-data" / "raster"

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

def zone_of(tile: str) -> str:
    """The tile key's UTM zone, zero-padded: ``01`` from ``01KFS_0_0``.

    Every one of the 67,197 tile keys matches ``\\d{2}[A-Z]{3}_\\d+_\\d+``
    (measured), so the two leading characters are always the padded zone and
    slicing beats parsing.
    """
    return tile[:2]


def gzd_of(tile: str) -> str:
    """The grid zone designator: ``01K`` from ``01KFS_0_0``.

    The zone plus the latitude-band letter. The two characters after it are
    the 100-km square, which is one level finer than this tree groups.
    """
    return tile[:3]


def group_key(year: int, tile: str) -> str:
    """The object-key prefix of the GZD catalog one tile groups under."""
    return f"raster/{year}/zone={zone_of(tile)}/gzd={gzd_of(tile)}"


def item_dir_key(year: int, tile: str) -> str:
    """The object-key prefix of one item's own directory.

    Data and metadata share it: the COG, the item JSON and the thumbnail are
    the three objects in here (user ruling 2026-10-02).
    """
    return f"{group_key(year, tile)}/{tile}"


# The key layouts this tile tree has had, newest first. The header pass can
# read a COG from any of them, because it runs while a server-side copy into
# the newest one is in flight: "grouped" is the published layout, "folder" is
# the per-item folder it supersedes, and "flat" is the original.
LAYOUTS = ("grouped", "folder", "flat")


def cog_key(year: int, tile: str, layout: str = "grouped") -> str:
    """The object key of one COG under the named layout."""
    if layout == "grouped":
        return f"{item_dir_key(year, tile)}/{tile}.tif"
    if layout == "folder":
        return f"raster/{year}/{tile}/{tile}.tif"
    return f"raster/{year}/{tile}.tif"


def cog_url(year: int, tile: str, layout: str = "grouped") -> str:
    """The public URL of one COG under the named layout."""
    return f"{PUBLIC_BASE}/{cog_key(year, tile, layout)}"


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
    registered as assets. The ``{sample_tile}/`` objects are probed because
    the docs describe the per-item directory and give a runnable ``gdalinfo``
    command: one tile's directory proves the published layout is live, so the
    docs describe what is there rather than what is planned. ``cog_folder``
    is the same tile under the layout the grouped keys supersede, so that
    while the server-side copy is in flight the docs can still name a key
    that answers today.
    """
    base = f"{PUBLIC_BASE}/raster/{year}"
    tile_dir = f"{PUBLIC_BASE}/{item_dir_key(year, sample_tile)}"
    return {
        "overview": f"{base}/overview.tif",
        "thumbnail.webp": f"{base}/thumbnail.webp",
        "items.parquet": f"{base}/items.parquet",
        "cog": f"{tile_dir}/{sample_tile}.tif",
        "item": f"{tile_dir}/{sample_tile}.json",
        "thumb": f"{tile_dir}/{sample_tile}.thumb.png",
        "cog_folder": cog_url(year, sample_tile, "folder"),
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
        for key in ("cog", "item", "thumb", "cog_folder")
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


GROUPED_PATH = "raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif"


def cog_rel_path(year: int, report: dict) -> str:
    """Where the COGs are *now*, in documentation form.

    The published layout groups every tile under ``zone=``/``gzd=``, and the
    server-side copy into those keys runs separately. Until a probed tile
    directory answers, the docs name the key that actually resolves; a re-run
    switches them over. No sentence here describes a key that 404s.
    """
    if report.get("cog") == "PRESENT":
        return GROUPED_PATH.replace("{year}", str(year))
    if report.get("cog_folder") == "PRESENT":
        return f"raster/{year}/{{tile}}/{{tile}}.tif"
    return f"raster/{year}/{{tile}}.tif"


def zone_children(year_dir: Path) -> list[dict]:
    """``child`` links to the zone catalogs that exist on disk.

    Read from the directory rather than from the index, for the same reason
    the bucket-side assets are probed: a ``child`` link to a catalog that is
    not there is a PTL-LNK-006 error, and a zone catalog on disk with no
    ``child`` link to it is a PTL-LNK-002 error. Deriving the links from the
    tree makes both impossible — run ``items`` first, then ``collections``.
    """
    links = []
    for path in sorted(year_dir.glob("zone=*/catalog.json")):
        zone = path.parent.name.removeprefix("zone=")
        links.append({
            "rel": "child", "href": f"./{path.parent.name}/catalog.json",
            "type": "application/json",
            "title": f"UTM zone {int(zone)}",
        })
    return links


def build_collection(year: int, stats: dict, extra: dict[str, dict],
                     report: dict, children: list[dict] | None = None) -> dict:
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
    children = children or []
    browse = ""
    if children:
        browse = (
            f"\n\n**Browsing.** The {stats['n']:,} items are grouped into "
            f"{len(children)} UTM-zone subcatalogs, each splitting into its "
            "grid zone designators (`zone=33/gzd=33U/…`), so every item is "
            "reachable by `child`/`item` links without any one object "
            "carrying thousands of them."
        )
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
            f"Each tile's STAC item sits beside its COG and thumbnail; the "
            f"[index manifest]({INDEX_URL}) lists every tile with href, "
            "size, bbox, and per-tile field/boundary/cropland pixel "
            f"fractions.{browse}"
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
            *children,
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
            # raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/ is five directories
            # below the catalog root; the GZD catalog is the containing
            # object, and the collection sits three above.
            {"rel": "root", "href": "../../../../../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (beta)"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json",
             "title": f"Grid zone {gzd_of(tile)} — {year}"},
            {"rel": "collection", "href": "../../../collection.json",
             "type": "application/json",
             "title": f"FTW Global — Field & Boundary Probabilities {year} "
                      "(COG)"},
            {"rel": "derived_from", "href": MOSAICS_URL, "type": "text/html",
             "title": "TGE Labs Sentinel-2 quarterly cloudless mosaics "
                      "(source item ids in ftw:source_items)"},
        ],
    }


# ── the zone / GZD browse tree ───────────────────────────────────────────────

def build_zone_catalog(year: int, zone: str, gzds: dict[str, list[str]],
                       ) -> dict:
    """One UTM zone's catalog: a child per grid zone designator in it."""
    tiles = sum(len(v) for v in gzds.values())
    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT],
        "id": f"ftw-raster-{year}-zone-{zone}",
        "title": f"UTM zone {int(zone)} — {year}",
        "description": (
            f"The {tiles:,} field/boundary probability tiles of {year} that "
            f"fall in UTM zone {int(zone)}, grouped by grid zone designator "
            f"({len(gzds)} of them: "
            + ", ".join(f"`{g}`" for g in sorted(gzds)) + "). "
            f"Part of [FTW Global — Field & Boundary Probabilities {year}]"
            "(../collection.json)."
        ),
        "links": [
            {"rel": "root", "href": "../../../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (beta)"},
            {"rel": "parent", "href": "../collection.json",
             "type": "application/json",
             "title": f"FTW Global — Field & Boundary Probabilities {year} "
                      "(COG)"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Zone README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Zone agent guide"},
            *[{"rel": "child", "href": f"./gzd={gzd}/catalog.json",
               "type": "application/json", "title": f"Grid zone {gzd}"}
              for gzd in sorted(gzds)],
        ],
    }


def build_gzd_catalog(year: int, zone: str, gzd: str,
                      tiles: list[str]) -> dict:
    """One grid zone designator's catalog: an item link per tile in it."""
    band = gzd[2]
    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT],
        "id": f"ftw-raster-{year}-{gzd.lower()}",
        "title": f"Grid zone {gzd} — {year}",
        "description": (
            f"The {len(tiles):,} field/boundary probability tiles of {year} "
            f"in grid zone designator `{gzd}` — UTM zone {int(zone)}, "
            f"latitude band {band}. Each tile's directory holds its COG, its "
            "STAC item and its thumbnail. Part of "
            f"[UTM zone {int(zone)} — {year}](../catalog.json)."
        ),
        "links": [
            {"rel": "root", "href": "../../../../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (beta)"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json",
             "title": f"UTM zone {int(zone)} — {year}"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Grid zone README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Grid zone agent guide"},
            *[{"rel": "item", "href": f"./{tile}/{tile}.json",
               "type": "application/geo+json",
               "title": f"{tile} — {year}"} for tile in tiles],
        ],
    }


def _group_docs(kind: str, title: str, body: list[str],
                queries: list[str]) -> tuple[str, str]:
    """README.md and AGENTS.md for one group catalog.

    Short, but every line is a measured fact about *this* group, not a stub:
    PTL-FIL-004 wants a title heading and content, and the documentation
    contract wants no unedited boilerplate.
    """
    readme = "\n".join([
        f"# {title}", "", *body, "",
        "Data license: "
        "[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). "
        "Produced by Taylor Geospatial from the "
        f"[Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}).", "",
        "## Band semantics", "",
        "Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so "
        f"probability = value × 1/255. {GSD} m, per-tile UTM CRS. The "
        "collection's [README](../../README.md) has the full band table.", "",
    ])
    agents = "\n".join([
        f"# AGENTS.md — {title}", "",
        f"Guidance for AI agents. This is a {kind} browse catalog: it exists "
        "so the items are reachable by `child`/`item` links. Every number "
        "here is measured from `index/raster.parquet`.", "",
        *body, "",
        "- Probability = pixel value × 1/255; band 1 `field`, band 2 "
        "`boundary`; no nodata is declared.",
        "- To enumerate tiles in bulk, query the collection's "
        "`items.parquet` mirror or the index manifest rather than walking "
        "these catalogs.", "",
        *queries,
    ])
    return readme, agents


def zone_docs(year: int, zone: str, gzds: dict[str, list[str]],
              ) -> tuple[str, str]:
    tiles = sum(len(v) for v in gzds.values())
    body = [
        f"- {tiles:,} tiles in {len(gzds)} grid zone designators: "
        + ", ".join(f"[`{g}`](./gzd={g}/catalog.json)" for g in sorted(gzds)),
        f"- UTM zone {int(zone)}, {year}. Parent collection: "
        "[FTW Global — Field & Boundary Probabilities "
        f"{year}](../collection.json)",
    ]
    return _group_docs("UTM-zone", f"UTM zone {int(zone)} — {year}", body, [])


def gzd_docs(year: int, zone: str, gzd: str, tiles: list[str],
             ) -> tuple[str, str]:
    body = [
        f"- {len(tiles):,} tiles, UTM zone {int(zone)}, latitude band "
        f"{gzd[2]}, {year}.",
        "- Each tile directory holds `{tile}.tif` (the COG), "
        "`{tile}.json` (its STAC item) and `{tile}.thumb.png`.",
        f"- Tile keys here run from `{tiles[0]}` to `{tiles[-1]}`.",
        f"- Parent: [UTM zone {int(zone)} — {year}](../catalog.json).",
    ]
    sample = tiles[0]
    queries = [
        "Read one tile:", "",
        "```bash",
        f"gdalinfo /vsicurl/{PUBLIC_BASE}/"
        f"{item_dir_key(year, sample)}/{sample}.tif",
        "```", "",
    ]
    return _group_docs("grid-zone", f"Grid zone {gzd} — {year}", body,
                       queries)


def group_tiles(tiles: list[str]) -> dict[str, dict[str, list[str]]]:
    """``{zone: {gzd: [tile, ...]}}`` for one year's tile keys."""
    out: dict[str, dict[str, list[str]]] = {}
    for tile in sorted(tiles):
        out.setdefault(zone_of(tile), {}).setdefault(
            gzd_of(tile), []).append(tile)
    return out


def write_group_tree(year_dir: Path, year: int,
                     tiles: list[str]) -> tuple[int, int]:
    """Write the zone and GZD catalogs (and their docs) for one year.

    Returns (zone catalogs, GZD catalogs).
    """
    groups = group_tiles(tiles)
    for zone, gzds in sorted(groups.items()):
        zone_dir = year_dir / f"zone={zone}"
        write_json(zone_dir / "catalog.json",
                   build_zone_catalog(year, zone, gzds))
        readme, agents = zone_docs(year, zone, gzds)
        (zone_dir / "README.md").write_text(readme)
        (zone_dir / "AGENTS.md").write_text(agents)
        for gzd, members in sorted(gzds.items()):
            gzd_dir = zone_dir / f"gzd={gzd}"
            write_json(gzd_dir / "catalog.json",
                       build_gzd_catalog(year, zone, gzd, members))
            readme, agents = gzd_docs(year, zone, gzd, members)
            (gzd_dir / "README.md").write_text(readme)
            (gzd_dir / "AGENTS.md").write_text(agents)
    return len(groups), sum(len(g) for g in groups.values())


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
        layouts = LAYOUTS if args.layout == "auto" else (args.layout,)
        last: Exception | None = None
        for layout in layouts:
            try:
                header = read_header(cog_url(year, tile, layout))
            except Exception as exc:  # noqa: BLE001 - try the next layout
                last = exc
                continue
            header |= {"year": year, "tile_key": tile, "layout": layout}
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

def build_mirror(out: Path, years: list[int],
                 mirror_dir: Path = MIRROR_DIR) -> int:
    """Run ``portolan stac-geoparquet`` over the committed tree, per year.

    ``-c`` names the collection by its **directory path from the catalog
    root**, not by its STAC id (measured: ``-c ftw-raster-2017`` and
    ``-c 2017`` both report "Collection not found", ``-c raster/2017``
    works and finds all 7,466 items nested two levels down).

    portolan writes the parquet next to the collection, which would drop a
    data file into ``catalog/``. The clean publish-directory model does not
    allow that, so it is moved straight out to ``mirror_dir``, where
    tools/upload_data.py publishes it from.

    It discovers the items by following the collection's ``child`` links
    (measured: with the zone links removed it reports "No items found" even
    though the item files are right there), so ``collections`` has to have
    run first. It also rewrites the mirror asset's ``file:size`` to the
    parquet it just wrote; re-running ``collections`` afterwards puts the
    published object's size back.
    """
    import subprocess

    catalog_root = out.parent  # .../catalog, since out is .../catalog/raster
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
            [str(portolan), "stac-geoparquet", "--catalog", str(catalog_root),
             "-c", f"{out.name}/{year}"],
            capture_output=True, text=True,
        )
        written = out / str(year) / "items.parquet"
        if proc.returncode != 0 or not written.is_file():
            failures += 1
            detail = (proc.stderr or proc.stdout).strip()[:400]
            print(f"  mirror {year} FAILED: {detail}", file=sys.stderr)
            if "No items found" in detail:
                print("    the collection reaches its items through the zone "
                      "`child` links; run `collections` first.",
                      file=sys.stderr)
            continue
        destination = mirror_dir / str(year) / "items.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        written.replace(destination)
        print(f"  mirror {year}: "
              f"{destination.stat().st_size / 2**20:.1f} MiB "
              f"-> {shown(destination)}")
    return failures


# ── the item uploader ────────────────────────────────────────────────────────

def item_uploads(out: Path, prefix: str) -> list[Upload]:
    """Every generated item JSON, with the object key it publishes to.

    One rule, and it is narrow on purpose: a file at
    ``{out}/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.json`` publishes to
    ``{prefix}/raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.json``. The
    file name must equal its directory name, so the group catalogs
    (``catalog.json`` in a ``gzd=…`` directory) and the year
    ``collection.json`` cannot match, and the zone/GZD components must carry
    their hive prefixes. The grouped keys are derived from the tile key, not
    from the path, so a stray directory cannot smuggle a key through.

    This exists beside tools/publish.py rather than replacing it: publish.py
    lists each directory non-recursively, which over 67,197 item directories
    is 67,197 listings, while this uploader lists each year recursively
    (nine listings) — see :func:`item_remote_index`.
    """
    uploads = []
    for path in sorted(out.glob("*/zone=*/gzd=*/*/*.json")):
        tile_dir = path.parent
        tile = tile_dir.name
        if path.stem != tile:
            continue
        year = tile_dir.parent.parent.parent.name
        if not year.isdigit():
            continue
        expected = (f"zone={zone_of(tile)}", f"gzd={gzd_of(tile)}")
        if (tile_dir.parent.parent.name, tile_dir.parent.name) != expected:
            continue
        key = f"{item_dir_key(int(year), tile)}/{path.name}"
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
    print(f"target: s3://{bucket}/{prefix}/"
          "raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/")
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


def cmd_mirror(args) -> int:
    """Rebuild each year's items.parquet from the committed item tree."""
    years = sorted(args.year) if args.year else [
        int(p.name) for p in sorted(args.out.iterdir())
        if p.is_dir() and p.name.isdigit()
    ]
    if not years:
        print(f"no year directories under {shown(args.out)}")
        return 1
    failures = build_mirror(args.out, years, args.mirror_dir)
    if failures:
        print(f"\n{failures} of {len(years)} mirror(s) failed",
              file=sys.stderr)
        return 1
    print("\nre-run `collections` so each mirror asset carries the published "
          "object's size, and upload with tools/upload_data.py")
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
        year = row["year"]
        write_json(
            args.out / str(year) / f"zone={zone_of(tile)}"
            / f"gzd={gzd_of(tile)}" / tile / f"{tile}.json",
            build_item(row, header, not args.no_thumbnails))
        written.setdefault(year, []).append(tile)

    for year in sorted(written):
        zones, gzds = write_group_tree(args.out / str(year), year,
                                       written[year])
        print(f"{year}: {len(written[year]):,} item(s) in {zones} zone / "
              f"{gzds} GZD catalog(s) -> {args.out / str(year)}")
    if missing:
        print(f"note: {missing:,} tile(s) have no header yet; run the "
              "`headers` subcommand (it is resumable) and re-run this.")
    if deviating:
        print(f"note: {deviating:,} tile(s) deviate from the documented band "
              "facts and were skipped; inspect the sidecar's `deviations` "
              "field, or pass --allow-deviations.", file=sys.stderr)

    if written and args.mirror:
        build_mirror(args.out, list(written), args.mirror_dir)

    if args.confirm or args.dry_run_upload:
        return upload_items(args.out, args.confirm, args.force)
    if not written:
        return 1
    print("\nitems and group catalogs are committed; run `collections` next "
          "so each year links the zone catalogs that now exist, then upload "
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
    """The grouped layout, showing only the files that are there."""
    sample = report.get("sample_tile", "01KFS_0_0")
    group = f"raster/{year}/zone={zone_of(sample)}/gzd={gzd_of(sample)}"
    rows = [(f"{group}/{{tile}}/{{tile}}.tif", "the COG", "cog"),
            (f"{group}/{{tile}}/{{tile}}.json", "its STAC item", "item"),
            (f"{group}/{{tile}}/{{tile}}.thumb.png", "its thumbnail",
             "thumb")]
    live = [(path, note) for path, note, key in rows
            if report.get(key) == "PRESENT"]
    head = [
        "Tiles are grouped by UTM zone and grid zone designator, taken from "
        f"the tile key — `{sample}` is zone `{zone_of(sample)}`, grid zone "
        f"`{gzd_of(sample)}` — and each tile's own directory holds its COG, "
        "its STAC item and its thumbnail:", "",
    ]
    if not live:
        answers = (f"`raster/{year}/{{tile}}/{{tile}}.tif`"
                   if report.get("cog_folder") == "PRESENT"
                   else f"`raster/{year}/{{tile}}.tif`")
        return [
            *head,
            "```",
            f"{group}/{{tile}}/{{tile}}.tif   the COG",
            f"{group}/{{tile}}/{{tile}}.json  its STAC item",
            f"{group}/{{tile}}/{{tile}}.thumb.png  its thumbnail",
            "```", "",
            "The server-side copy into those keys is still running, so the "
            f"COGs currently answer at {answers} (for example "
            f"`{sample}.tif`).", "",
        ]
    width = max(len(path) for path, _ in live)
    return [
        *head,
        "```",
        *[f"{path:<{width}}  {note}" for path, note in live],
        "```", "",
    ]


def year_readme(year: int, stats: dict, extra: dict[str, dict],
                report: dict, children: list[dict] | None = None) -> str:
    children = children or []
    tb = stats["bytes"] / 1e12
    sample = stats["sample_tile"]
    layout = ("grouped" if report.get("cog") == "PRESENT"
              else "folder" if report.get("cog_folder") == "PRESENT"
              else "flat")
    read_url = cog_url(year, sample, layout)
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
    if children or "overview" in extra or "mirror" in extra:
        lines += ["## Browse it", ""]
        if children:
            lines += [
                f"The {stats['n']:,} items are grouped into "
                f"{len(children)} UTM-zone subcatalogs, each splitting into "
                "its grid zone designators, so every tile is reachable by "
                "`child`/`item` links a few clicks deep instead of through "
                "one list of thousands:", "",
                "```",
                f"{year}/collection.json",
                f"{year}/zone={{ZZ}}/catalog.json        "
                f"{len(children)} of these",
                f"{year}/zone={{ZZ}}/gzd={{GZD}}/catalog.json",
                f"{year}/zone={{ZZ}}/gzd={{GZD}}/{{tile}}/{{tile}}.json",
                "```", "",
                "The UTM zones present this year: "
                + ", ".join(link["href"].removeprefix("./zone=")
                            .removesuffix("/catalog.json")
                            for link in children)
                + f" — {len(children)} of the 60 UTM zones; the others hold "
                "no tiles in this collection.", "",
            ]
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
                report: dict, children: list[dict] | None = None) -> str:
    children = children or []
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
        f"(`year = {year}`)"
        + (" or the `items.parquet` mirror"
           if report.get("items.parquet") == "PRESENT" else "")
        + ", never by listing the bucket. The collection carries no "
        "`rel: item` link of its own: the items hang off "
        + (f"{len(children)} `zone={{ZZ}}/catalog.json` subcatalogs, each "
           "splitting into `gzd={GZD}/catalog.json`, which carry the item "
           "links. Walking that tree costs ~400 requests per year, so for "
           "bulk work read the mirror or the manifest instead."
           if children else
           "browse subcatalogs that `items` generates; run it and re-run "
           "`collections`."),
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
        "## Browsing", "",
        "Each year's tiles are grouped by UTM zone and grid zone designator, "
        "read straight off the tile key, and each tile's directory holds its "
        "COG, its STAC item and its thumbnail together:", "",
        "```",
        "raster/{year}/collection.json",
        "raster/{year}/zone={ZZ}/catalog.json",
        "raster/{year}/zone={ZZ}/gzd={GZD}/catalog.json",
        "raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif",
        "raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.json",
        "raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.thumb.png",
        "```", "",
        "So `01KFS_0_0` sits under `zone=01/gzd=01K/`. For bulk work, read a "
        f"year's `items.parquet` mirror or the [index manifest]({INDEX_URL}) "
        "rather than walking the tree.", "",
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
        + ", never by listing the bucket and never by walking the browse "
          "tree (~400 catalogs per year); read each year's AGENTS.md for "
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
        ("- Layout: `raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/` holds "
         + ", ".join(in_tile) + "; " if in_tile
         else "- Layout: the COGs answer at `raster/{year}/{tile}/{tile}.tif` "
              "while the copy into the grouped "
              "`zone={ZZ}/gzd={GZD}/{tile}/` keys runs; ")
        + "`raster/{year}/` holds " + ", ".join(beside)
        + ", and the zone catalogs that group the items.",
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


def shown(path: Path) -> str:
    """A path for messages: repo-relative when it is inside the repo.

    ``--out`` can point anywhere (a scratch tree, for a trial run), and a
    message is not worth a crash.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


STALE = ("llms.txt",)


def drop_stale(directory: Path) -> None:
    """Remove outputs this generator no longer emits."""
    for name in STALE:
        path = directory / name
        if path.is_file():
            path.unlink()
            print(f"removed {shown(path)}")


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
        children = zone_children(year_dir)
        if not children:
            print(f"note: {shown(year_dir)} has no zone catalog, "
                  "so the collection links no items. Run `items` first.")
        collection = build_collection(year, year_stats, extra, report,
                                      children)
        patch_local_assets(year_dir, collection)
        write_json(year_dir / "collection.json", collection)
        (year_dir / "README.md").write_text(
            year_readme(year, year_stats, extra, report, children))
        (year_dir / "AGENTS.md").write_text(
            year_agents(year, year_stats, extra, report, children))
        drop_stale(year_dir)
        # A webp downloaded by an earlier run, whose object has since gone,
        # would otherwise stay in catalog/ unregistered and still publish.
        if not args.no_probe and report["thumbnail.webp"] == "ABSENT":
            stale_webp = year_dir / "thumbnail.webp"
            if stale_webp.is_file():
                stale_webp.unlink()
                print(f"removed {shown(stale_webp)} "
                      "(its object is gone)")
        state = " ".join(f"{k}={v}" for k, v in report.items())
        print(f"{year}: {year_stats['n']:,} tiles, "
              f"{year_stats['bytes'] / 1e12:.2f} TB, "
              f"{len(children)} zone catalog(s)  [{state}]")

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
        "--staging", type=Path, default=MIRROR_DIR,
        help="where the items.parquet mirrors are, for their checksums")
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
    headers.add_argument("--layout", choices=("auto", *LAYOUTS),
                         default="auto",
                         help="which COG key to read (auto tries the "
                              "published grouped key, then the per-item "
                              "folder key, then the original flat key — the "
                              "older two stay live during a copy)")

    items = sub.add_parser(
        "items", help="build the per-tile items from the index + sidecar"
    )
    items.add_argument("--year", type=int, action="append")
    items.add_argument("--sidecar", type=Path, default=SIDECAR)
    items.add_argument("--out", type=Path, default=ITEMS_DIR)
    items.add_argument("--mirror-dir", type=Path, default=MIRROR_DIR,
                       help="where each year's items.parquet is written "
                            "(outside catalog/: it is a data file)")
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

    mirror = sub.add_parser(
        "mirror", help="rebuild items.parquet from the committed item tree"
    )
    mirror.add_argument("--year", type=int, action="append")
    mirror.add_argument("--out", type=Path, default=ITEMS_DIR)
    mirror.add_argument("--mirror-dir", type=Path, default=MIRROR_DIR)

    args = parser.parse_args()
    if args.command == "collections":
        return cmd_collections(args)
    if args.command == "headers":
        return cmd_headers(args)
    if args.command == "items":
        return cmd_items(args)
    if args.command == "mirror":
        return cmd_mirror(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
