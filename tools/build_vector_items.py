#!/usr/bin/env python3
"""Generate the vector tree of the 2e catalog: per-year source collections.

Emits, under ``catalog/vector/``:

- ``catalog.json`` — the vector subtree catalog (children: the year
  collections; ``fields-yearly`` is added by the Phase 3 tiles work).
- ``{year}/collection.json`` — one collection per year, id from the parquet's
  own embedded ``collection`` key (``ftw-s2-{year}``).
- ``{year}/zone={NN}/utm{NN}.json`` — one item per UTM-zone parquet, in its
  own hive-keyed subdirectory (PORTO-CORE-015), with ``table:columns`` for
  every column, ``file:size``/``file:checksum`` on the data asset, and an
  ``alternate`` s3 href (PORTO-CORE-024) whose bucket is described by
  ``storage:schemes`` (PORTO-FMT-066).
- README.md / AGENTS.md for the subtree and each collection, with
  measured numbers (counts and sizes come from the index, not prose memory).

Everything is derived from ``index/vector.parquet`` and the embedded parquet
metadata (schema links, per-column descriptions, processing notes), so a
regeneration is a no-op when nothing changed. Edit this generator, never the
generated JSON.

    .venv/bin/python3 tools/build_vector_items.py \
        --checksums staging-data/checksums/vector_checksums.json

Checksums come from ``tools/hash_remote.py`` (the bytes live only in the
bucket). Without ``--checksums`` the digests are read back out of the
committed items, so a re-run reproduces them; pass the sidecar whenever the
bytes have changed, because only it knows the new digests.

After generating, build each year's stac-geoparquet mirror:

    .venv/bin/portolan stac-geoparquet catalog/vector/2025

(The mirror asset is registered by this script; the parquet itself is
generated locally, published by tools/publish.py, and never committed.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_thumbnails import STYLE_BY_YEAR as THUMBNAIL_STYLE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BINS_FILE = ROOT / "pipeline" / "style_bins.json"
TILES_META = ROOT / "staging-data" / "checksums" / "tiles_meta.json"

PUBLIC_BASE = "https://data.source.coop/ftw/global-data-2e"
INDEX_URL = f"{PUBLIC_BASE}/index/vector.parquet"
# Read the index from FTW_INDEX_DIR/vector.parquet when set (an index that is not published yet);
# the default is the published copy. Docs keep linking INDEX_URL.
INDEX_SOURCE = (str(Path(os.environ["FTW_INDEX_DIR"]) / "vector.parquet")
                if os.environ.get("FTW_INDEX_DIR") else INDEX_URL)

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.3.0/schema.json"
WEBMAP_EXT = "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json"
PARTITION_EXT = "https://schemas.portolan-sdi.org/incubating/partition/v1.0.0/schema.json"
TABLE_EXT = "https://stac-extensions.github.io/table/v1.2.0/schema.json"
PROJ_EXT = "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
ALT_EXT = "https://stac-extensions.github.io/alternate-assets/v1.2.0/schema.json"
STORAGE_EXT = "https://stac-extensions.github.io/storage/v2.0.0/schema.json"

# The bucket behind every `s3://` href and behind `partition:glob`. Portolan
# v0.3 asks publishers to describe it with the storage extension rather than
# leave a client to guess the endpoint (PORTO-FMT-066).
#
# The bytes do sit in Amazon S3 in us-west-2 —
# `https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/...`
# answers 200 — but the type here is `custom-s3`, not `aws-s3`, and that is
# deliberate. The bucket name contains dots, so the virtual-host endpoint is
# the one thing a client must NOT use: it fails TLS verification. The
# extension's `aws-s3` profile pins `platform` to exactly
# `https://{bucket}.s3.{region}.amazonaws.com` (its schema rejects anything
# else, measured as PTL-EXT-001), which is that broken endpoint. The
# `custom-s3` profile takes an endpoint URL template instead, and its own
# mapping rules say a template with no `{bucket}` placeholder means
# path-style addressing (`AWS_VIRTUAL_HOSTING=FALSE`). So `custom-s3` is the
# profile that can state the access path that works.
S3_BUCKET = "us-west-2.opendata.source.coop"
S3_SCHEME_KEY = "source-coop-s3"
STORAGE_SCHEMES = {
    S3_SCHEME_KEY: {
        "type": "custom-s3",
        "platform": "https://s3.us-west-2.amazonaws.com",
        "bucket": S3_BUCKET,
        "region": "us-west-2",
        "requester_pays": False,
        "title": "Source Cooperative open-data bucket (S3, us-west-2)",
        "description": (
            "Anonymous read, no credentials needed (`--no-sign-request`). "
            "The bucket name contains dots, so clients must address it "
            "path-style, which is what this endpoint URL without a "
            "`{bucket}` placeholder asks for: `s3_url_style='path'` in "
            "DuckDB, `AWS_VIRTUAL_HOSTING=FALSE` in GDAL, `--endpoint-url "
            "https://s3.us-west-2.amazonaws.com` for the AWS CLI. The "
            "virtual-host form fails TLS verification."
        ),
    },
}

FIBOA_SPEC = "https://fiboa.org/specification/v0.3.0/schema.yaml"
VECOREL_SPEC = "https://vecorel.org/specification/v0.1.0/schema.yaml"
FIBOA_README = "https://github.com/fiboa/specification"
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
# renders the markdown at /ftw/global-data-2e, where a relative `./vector/...`
# resolves to /ftw/vector/... and 404s, which is what readers reported. STAC
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

# Verified against the parquet's embedded metadata (determination:details);
# fetched live per year by fetch_year_meta and asserted to still match.
_FIBOA = f"[fiboa 0.3.0]({FIBOA_SPEC})"
_VECOREL = f"[vecorel 0.1.0]({VECOREL_SPEC})"

# The published parquet footers carry this sentence, and it misleads twice.
# "Attributes are for filtering" describes columns this data does not have:
# `pipeline/postprocessing/context.py` warps IO 10 m annual land cover and
# the Copernicus GLO-30 DEM over every window, and `outlines.py` turns them
# into five per-parcel attributes (`frac_water`, `frac_crops_ever`,
# `slope_mean`, `frac_slope_gt30`, `elev_mean`), none of which survive into
# the released nine columns. "No land-cover masking" is true of individual
# parcels, since the only retention test is `in_utm_zone AND in_mgrs_square
# AND area_m2 <= 5e6` (`merge_polygons.py:222`), but it hides the bigger
# fact that land cover chose which tiles ran at all. Measured against the
# published index on 2026-10-03: every year's minimum `cropland_frac` is
# exactly 0.010006 with no tile below 1%, which is a threshold rather than a
# distribution. `fiboa_convert.py` no longer emits the sentence; this
# rewrite is for the parquet already in the bucket.
_STALE_DETAIL = (
    "Attributes are for filtering; no land-cover masking was applied."
)
_TRUE_DETAIL = (
    "Within a processed tile no parcel is removed on land-cover, water or "
    "terrain grounds, the retention test being UTM-zone and MGRS-square "
    "ownership plus the size bounds above. Land cover did decide which "
    "tiles ran: only MGRS tiles with at least 1% cropland were processed, "
    "so regions below that threshold are absent entirely."
)


#: The 2026-10 rerun (r1) footers name the water and sea rules this sentence denies.
_R1_MARKER = "io-lulc 2024 water"


def water_filtered(meta: dict) -> bool:
    "True for a year built by the r1 rerun, whose footer lists the water and sea rules."
    return _R1_MARKER in meta.get("determination:details", "")


def filter_note(meta: dict) -> str:
    "What post-processing removed inside a processed tile, from the year's own footer."
    if water_filtered(meta):
        return (
            "Inside a processed tile, parcels at least 70% io-lulc 2024 water (inland water, "
            "aquaculture ponds, salt pans) and parcels with less than half their area on "
            "OpenStreetMap land (sea) were removed; no other land-cover or terrain rule applies, "
            "so scrub and built-up ground can still carry predicted parcels."
        )
    return (
        "Inside a processed tile nothing is filtered by land cover, so water, scrub "
        "and built-up ground can carry predicted parcels."
    )


#: Years whose Sentinel-2 mosaics are sparse. The rerun parcel counts for these years are about
#: 9 to 12% below 2025 (qa/vector_{year}.json), and the gap tracks input nodata.
_SPARSE_YEARS = (2017, 2018, 2019)


def nodata_note(year: int) -> str:
    "The under-detection caveat for the sparse-mosaic years, empty for the rest."
    if year not in _SPARSE_YEARS:
        return ""
    return (
        "Where this year's Sentinel-2 mosaics have large nodata gaps, detections are probably "
        "under-reported even after the gaps were filled. Parcel counts for 2017, 2018 and 2019 "
        "are about 9–12% below 2025, and part of that gap reflects the missing imagery."
    )


def fix_details(details: str) -> str:
    """Correct the land-cover sentence the published parquet footers carry."""
    if _STALE_DETAIL not in details:
        return details
    return details.replace(_STALE_DETAIL, _TRUE_DETAIL)


# The 9 columns of every zone parquet (second bucket revision, 2026-09-28).
# The `score` description is the dataset's own, from the parquet's embedded
# schemas:custom; the core fields follow fiboa/vecorel.
TABLE_COLUMNS = [
    {"name": "id", "type": "string",
     "description": f"Parcel identifier, unique per collection ({_FIBOA})."},
    {"name": "collection", "type": "string",
     "description": "The vecorel collection the parcel belongs to "
                    "(constant per year, e.g. `ftw-s2-2025`)."},
    {"name": "geometry", "type": "binary",
     "description": "Parcel footprint (WKB Polygon/MultiPolygon, WGS 84)."},
    {"name": "bbox", "type": "struct",
     "description": "Bounding box of the parcel (xmin, ymin, xmax, ymax)."},
    {"name": "metrics:area", "type": "float",
     "description": f"Area of the parcel in square meters ({_VECOREL} "
                    "geometry-metrics)."},
    {"name": "metrics:perimeter", "type": "float",
     "description": f"Perimeter of the parcel in meters ({_VECOREL} "
                    "geometry-metrics)."},
    {"name": "score", "type": "uint8",
     "description": "Mean model field probability inside the parcel, × 100 "
                    "rounded (0–100)."},
    {"name": "determination:datetime", "type": "timestamp",
     "description": "The prediction year's UTC start marker, constant per "
                    f"year ({_FIBOA})."},
    {"name": "determination:method", "type": "string",
     "description": f"Constant `auto-imagery` ({_FIBOA})."},
]

_COLS_SHORT = ", ".join(c["name"] for c in TABLE_COLUMNS)


def connect():
    import duckdb

    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-global-data-catalog)"}
    )
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET http_retries=20;")
    return con


def read_index(con) -> list[dict]:
    rows = con.execute(f"""
        SELECT year, zone, href, s3_href, size_bytes, n_parcels, area_km2,
               xmin, ymin, xmax, ymax, ST_AsGeoJSON(geometry) AS geom
        FROM '{INDEX_SOURCE}' ORDER BY year, zone
    """).fetchall()
    cols = ("year", "zone", "href", "s3_href", "size_bytes", "n_parcels",
            "area_km2", "xmin", "ymin", "xmax", "ymax", "geom")
    return [dict(zip(cols, r)) for r in rows]


def fetch_year_meta(con, year: int, url: str) -> dict:
    """The embedded vecorel collection metadata for one year's parquet.

    Read from a zone file's footer (constant across zones); carries the
    collection id, determination:* processing notes, and schema links.
    """
    (value,) = con.execute(f"""
        SELECT value::VARCHAR FROM parquet_kv_metadata('{url}')
        WHERE key::VARCHAR = 'collection'
    """).fetchall()[0]
    meta = json.loads(value.encode().decode("unicode_escape")
                      .replace('\\x22', '"'))
    cid = meta["collection"]
    if cid != f"ftw-s2-{year}":
        sys.exit(f"unexpected collection id for {year}: {cid}")
    return meta


GREEN = "#33a02c"


def step(prop_expr, colors: list[str], edges: list[float]) -> list:
    """A MapLibre step expression: len(colors) == len(edges) + 1."""
    assert len(colors) == len(edges) + 1, (colors, edges)
    out = ["step", prop_expr, colors[0]]
    for edge, color in zip(edges, colors[1:]):
        out += [edge, color]
    return out


def style_specs() -> dict[str, dict]:
    """The four per-year styles; bins are the checkpoint-approved edges."""
    bins = json.loads(BINS_FILE.read_text())
    return {
        "count": {
            "title": "Field count (A5 r7 → fields)",
            "description": "Fields per A5 r7 cell at z0–8 (stepped bins), "
                           "handing over to the actual {year} field "
                           "polygons from z9.",
            "cell_prop": ["get", "count"], "field_prop": None,
            **bins["count"],
        },
        "coverage": {
            "title": "Field coverage (A5 r7 → fields)",
            "description": "Percent of each A5 r7 cell covered by {year} "
                           "fields at z0–8, handing over to the actual "
                           "field polygons from z9.",
            "cell_prop": ["get", "pct_covered"], "field_prop": None,
            **bins["coverage"],
        },
        "avg-size": {
            "title": "Mean field size (A5 r7 → fields)",
            "description": "Mean field size per A5 r7 cell (hectares, "
                           "stepped bins) at z0–8; from z9 each {year} "
                           "field polygon is colored by its own size on "
                           "the same bins.",
            "cell_prop": ["/", ["get", "area_ha"],
                          ["max", ["get", "count"], 1]],
            "field_prop": ["/", ["get", "metrics:area"], 10000],
            **bins["avg-size"],
        },
        "field-prob": {
            "title": "Field probability (A5 r7 → fields)",
            "description": "Mean parcel score (model field probability × "
                           "100) per A5 r7 cell at z0–8; from z9 each "
                           "{year} field polygon is colored by its own "
                           "score on the same bins.",
            "cell_prop": ["get", "avg_score"],
            "field_prop": ["get", "score"],
            **bins["field-prob"],
        },
    }


def build_style(year: int, name: str, spec: dict) -> dict:
    """One style: cells choropleth to z9, fields beyond."""
    url = f"pmtiles://{PUBLIC_BASE}/vector/{year}/fields-{year}.pmtiles"
    cells = {
        "id": "cells-fill", "type": "fill", "source": "data",
        "source-layer": "cells", "maxzoom": 9,
        "paint": {
            "fill-color": step(spec["cell_prop"], spec["colors"],
                               spec["edges"]),
            "fill-opacity": 0.8,
        },
    }
    if spec.get("field_prop") is not None:
        fields = [{
            "id": "fields-fill", "type": "fill", "source": "data",
            "source-layer": "fields", "minzoom": 9,
            "paint": {
                "fill-color": step(spec["field_prop"], spec["colors"],
                                   spec["edges"]),
                "fill-opacity": 0.7,
            },
        }]
    else:
        fields = [
            {"id": "fields-fill", "type": "fill", "source": "data",
             "source-layer": "fields", "minzoom": 9,
             "paint": {"fill-color": GREEN, "fill-opacity": 0.25}},
            {"id": "fields-outline", "type": "line", "source": "data",
             "source-layer": "fields", "minzoom": 9,
             "paint": {"line-color": GREEN, "line-width": 1}},
        ]
    return {
        "version": 8,
        "name": f"{spec['title']} ({year})",
        "metadata": {"description": spec["description"].format(year=year)},
        "sources": {"data": {"type": "vector", "url": url}},
        "layers": [cells, *fields],
    }


def zone_stem(zone: int) -> str:
    return f"utm{zone:02d}"


def human_bytes(size: int) -> str:
    """A size in KiB, MiB or GiB, whichever reads sensibly.

    The zone parquets span four orders of magnitude — 288 KiB for zone 57 in
    2017, 9.4 GiB for zone 48 in 2021 — so one fixed unit prints either
    `0 MiB` or a five-digit MiB number. Over the 486 committed items this
    gives KiB for 12, MiB for 225 and GiB for 249.
    """
    kib = size / 2**10
    if kib < 1024:
        return f"{kib:,.0f} KiB"
    mib = kib / 1024
    if mib < 1024:
        return f"{mib:,.1f} MiB" if mib < 10 else f"{mib:,.0f} MiB"
    return f"{mib / 1024:,.1f} GiB"


def build_item(row: dict, meta: dict, checksums: dict) -> dict:
    year, zone = row["year"], row["zone"]
    stem = zone_stem(zone)
    title = f"UTM zone {zone} — {year} field boundaries"
    data_asset = {
        "href": f"./{stem}.parquet",
        "type": "application/vnd.apache.parquet",
        "title": f"{title} (GeoParquet)",
        "description": (
            f"One zone partition of the {year} collection: "
            f"{row['n_parcels']:,} predicted field parcels in UTM zone "
            f"{zone}, WGS 84, {human_bytes(row['size_bytes'])}. "
            "Columns are described in this item's `table:columns`."
        ),
        "roles": ["data"],
        "file:size": row["size_bytes"],
        # The `s3` alternate carries no `storage:refs`, and the item declares
        # no `storage:schemes`: the scheme is declared once, on the
        # collection, where `partition:glob` needs it (PORTO-FMT-066).
        # Repeating the block in every item would add it 486 times here and
        # 67,197 times in the raster tree to say the same thing.
        "alternate": {
            "s3": {
                "href": row["s3_href"],
                "title": "S3 URI (us-west-2, anonymous read)",
            }
        },
    }
    entry = checksums.get(row["href"])
    if entry:
        if entry["size"] != row["size_bytes"]:
            sys.exit(f"{row['href']}: checksum sidecar size {entry['size']} "
                     f"!= index size {row['size_bytes']}")
        data_asset["file:checksum"] = entry["checksum"]
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "stac_extensions": [PROJ_EXT, TABLE_EXT, FILE_EXT, ALT_EXT],
        "id": stem,
        "geometry": json.loads(row["geom"]),
        "bbox": [row["xmin"], row["ymin"], row["xmax"], row["ymax"]],
        "properties": {
            "title": title,
            "description": (
                f"Predicted field boundaries falling in UTM zone {zone} for "
                f"{year}: {row['n_parcels']:,} parcels, "
                f"{row['area_km2']:,.0f} km² of field area, in one "
                f"cloud-native GeoParquet file. {_PROJECT} Columns: "
                f"{_COLS_SHORT} — see `table:columns` for definitions."
            ),
            "datetime": f"{year}-01-01T00:00:00Z",
            "start_datetime": f"{year}-01-01T00:00:00Z",
            "end_datetime": f"{year}-12-31T23:59:59Z",
            "proj:code": "EPSG:4326",
            "table:row_count": row["n_parcels"],
            "table:columns": TABLE_COLUMNS,
        },
        "collection": meta["collection"],
        "assets": {"data": data_asset},
        "links": [
            {"rel": "root", "href": "../../../catalog.json",
             "type": "application/json"},
            {"rel": "parent", "href": "../collection.json",
             "type": "application/json"},
            {"rel": "collection", "href": "../collection.json",
             "type": "application/json"},
        ],
    }


def committed_item_checksums(out: Path, index: list[dict]) -> dict:
    """url -> size/checksum, read back from the committed zone items.

    The same fallback `tiles_meta_for` gives the collection assets, for the
    486 per-zone data assets. `tools/hash_remote.py` wrote those digests from
    the published bytes, and its sidecar lives under the gitignored staging
    tree, so a checkout has the digests only inside the items it already
    carries. Without this, a re-run with no `--checksums` silently dropped
    `file:checksum` from every item — the generator is supposed to reproduce
    the committed tree, not strip it.

    An entry whose size disagrees with the index is left out: the index then
    describes a *different* file at that key, and its digest is unknown here.
    An explicit `--checksums` sidecar is still checked strictly in
    `build_item`, because passing one is a claim that it matches.
    """
    by_url = {row["href"]: row["size_bytes"] for row in index}
    found: dict[str, dict] = {}
    stale = 0
    for item_path in sorted(out.glob("*/zone=*/utm*.json")):
        try:
            asset = json.loads(item_path.read_text())["assets"]["data"]
        except (KeyError, json.JSONDecodeError):
            continue
        if not asset.get("file:checksum"):
            continue
        rel = item_path.relative_to(out).with_suffix(".parquet")
        url = f"{PUBLIC_BASE}/vector/{rel.as_posix()}"
        if by_url.get(url) != asset.get("file:size"):
            stale += 1
            continue
        found[url] = {"size": asset["file:size"],
                      "checksum": asset["file:checksum"]}
    if stale:
        print(f"note: {stale} committed item checksum(s) describe a file the "
              "index no longer lists at that key; not reused")
    return found


# (tiles_meta key prefix, collection asset key) for the assets whose bytes live only in the bucket.
# `cellsmulti` is vector/{year}/cells-{year}.pmtiles, the multi-resolution cell archive the web
# viewer reads. The asset is written for the years that have an entry in tiles_meta.json.
META_ASSETS = (("pmtiles", "pmtiles"), ("cells", "cells"),
               ("cellsmulti", "cells-pmtiles"), ("mirror", "mirror"))


def tiles_meta_for(year: int, out: Path | None = None) -> dict:
    """size+checksum for the year's assets whose bytes live only in the bucket.

    The staging sidecar wins where it has the year, because the PMTiles build
    writes it. It is gitignored, though, so a checkout without it (or with only
    some years in it) falls back to the committed `collection.json`. Without
    that fallback a regeneration silently drops the `pmtiles`, `cells` and
    `styles/*` assets for every missing year — `has_tiles` gates all three — so
    the advertised "edit the generator and re-run it" would delete metadata
    rather than reproduce it.
    """
    sizes = (json.loads(TILES_META.read_text())
             if TILES_META.is_file() else {})
    base = out if out is not None else ROOT / "catalog" / "vector"
    committed = base / str(year) / "collection.json"
    if not committed.is_file():
        return sizes
    assets = json.loads(committed.read_text()).get("assets", {})
    for key, asset_key in META_ASSETS:
        asset = assets.get(asset_key) or {}
        if f"{key}_{year}" in sizes or not asset.get("file:checksum"):
            continue
        sizes[f"{key}_{year}"] = {"size": asset["file:size"],
                                  "checksum": asset["file:checksum"]}
    return sizes


def build_collection(year: int, rows: list[dict], meta: dict) -> dict:
    n = sum(r["n_parcels"] for r in rows)
    gib = sum(r["size_bytes"] for r in rows) / 2**30
    area = sum(r["area_km2"] for r in rows)
    bbox = [min(r["xmin"] for r in rows), min(r["ymin"] for r in rows),
            max(r["xmax"] for r in rows), max(r["ymax"] for r in rows)]
    tiles_meta = tiles_meta_for(year)
    has_tiles = f"pmtiles_{year}" in tiles_meta
    links = [
        {"rel": "root", "href": "../../catalog.json",
         "type": "application/json",
         "title": "Fields of the World — Global Data"},
        {"rel": "parent", "href": "../catalog.json",
         "type": "application/json"},
        {"rel": "license",
         "href": "https://creativecommons.org/licenses/by/4.0/",
         "type": "text/html", "title": "CC-BY-4.0"},
        {"rel": "derived_from", "href": MOSAICS_URL, "type": "text/html",
         "title": "TGE Labs Sentinel-2 quarterly cloudless mosaics"},
        {"rel": "describedby", "href": "./README.md", "type": "text/markdown",
         "title": "Collection README"},
        {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
         "title": "Collection agent guide"},
        {"rel": "describedby", "href": FIBOA_README, "type": "text/html",
         "title": "fiboa specification (core parcel fields)"},
        # No rel:"via" for the map: Portolan reserves it for mirrored source
        # data (PTL-PRO-004), and this collection is the source.
        {"rel": "vcs", "href": REPO_URL, "type": "text/html",
         "title": "Catalog source repository (metadata and pipeline)"},
    ]
    if has_tiles:
        links.append({
            "rel": "pmtiles", "href": f"./fields-{year}.pmtiles",
            "type": "application/vnd.pmtiles",
            "title": f"Fields {year} (cells z0–8 → fields z9–13)",
            "pmtiles:layers": ["cells", "fields"]})
    for row in rows:
        stem = zone_stem(row["zone"])
        links.append({
            "rel": "item",
            "href": f"./zone={row['zone']:02d}/{stem}.json",
            "type": "application/geo+json",
            "title": f"UTM zone {row['zone']}",
        })
    specs = style_specs()
    # The glob lives in `partition:glob` and nowhere else. Portolan v0.3 gives
    # a data asset exactly one file (PORTO-FMT-065) and requires the glob to
    # be an absolute URL a client can *list* (PORTO-FMT-064/075), which rules
    # out both the relative path this field used to carry and the https glob
    # that used to sit in a `data` asset beside it: HTTP has no list call, so
    # the wildcard was never expandable from that href. The one href a reader
    # pastes into DuckDB is the s3 form below, and the description names it.
    s3_glob = (f"s3://{S3_BUCKET}/ftw/global-data-2e/"
               f"vector/{year}/zone=*/utm*.parquet")
    assets: dict[str, dict] = {}
    if has_tiles:
      assets["pmtiles"] = {
            "href": f"./fields-{year}.pmtiles",
            "type": "application/vnd.pmtiles",
            "title": f"Fields {year} — A5 r7 cells z0–8 → field polygons "
                     "z9–13 (PMTiles)",
            "description": "The year as vector tiles for web maps: A5 r7 "
                           "cell aggregates from z0 to z8, handing over to "
                           "the field polygons themselves from z9 to z13. "
                           "Layers `cells` and `fields`; draw it with one of "
                           "the `style` assets.",
            "roles": ["visual"],
      }
      assets["cells"] = {
            "href": f"./cells_a5r7_{year}.parquet",
            "type": "application/vnd.apache.parquet",
            "title": f"A5 r7 cell aggregates {year} (GeoParquet)",
            "description": "Per-cell count, area_ha, avg_score, "
                           "pct_covered — the z0–8 exploration layer, "
                           "also useful for analysis.",
            "roles": ["data"],
      }
    if has_tiles and f"cellsmulti_{year}" in tiles_meta:
        assets["cells-pmtiles"] = {
            "href": f"./cells-{year}.pmtiles",
            "type": "application/vnd.pmtiles",
            "title": f"A5 cell aggregates {year}, one resolution per zoom "
                     "(PMTiles)",
            "description": "A5 cells for drawing the year at global scale, "
                           "with one resolution per zoom: r4 at the globe "
                           "to r11 at zoom 7. Each cell carries count, "
                           "area_ha, avg_score, pct_covered and density "
                           "(fields per 1,000 km²). About 140 MB per year.",
            "roles": ["visual"],
        }
    for name, spec in (specs.items() if has_tiles else ()):
        roles = ["style", "default"] if name == "coverage" else ["style"]
        assets[f"styles/{name}"] = {
            "href": f"./styles/{name}.json",
            "type": "application/vnd.mapbox.style+json",
            "title": f"{spec['title']} ({year})"
                     + (" — default" if "default" in roles else ""),
            "description": ("MapLibre GL style (spec v8) over this year's "
                            "PMTiles. "
                            + spec["description"].format(year=year)),
            "roles": roles,
        }
    if has_tiles or (ROOT / "catalog" / "vector" / str(year)
                     / "thumbnail.png").is_file():
        style_name = THUMBNAIL_STYLE.get(year, "coverage")
        style_label = specs[style_name]["title"].split(" (")[0].lower()
        assets["thumbnail"] = {
            "href": "./thumbnail.png", "type": "image/png",
            "title": f"Fields {year} rendered with the "
                     f"{style_label} "
                     f"style",
            "description": (
                f"Global preview of the {year} fields, rendered from this "
                f"collection's PMTiles with the {style_label} style "
                "(`styles/" + style_name + "`)."
            ),
            "roles": ["thumbnail"],
        }
    assets["mirror"] = {
        "href": "./items.parquet",
        "type": "application/vnd.apache.parquet",
        "title": "STAC GeoParquet mirror of the items",
        "description": "All item metadata in one stac-geoparquet "
                       "file, for bulk queries. Derived from the "
                       "items; the item JSON stays normative.",
        "roles": ["collection-mirror", "metadata"],
    }
    # Sizes and checksums for the assets whose bytes live only in the bucket.
    # Stamped last, so every asset above is in place to receive them.
    for key, asset_key in META_ASSETS:
        entry = tiles_meta.get(f"{key}_{year}")
        if entry and asset_key in assets:
            assets[asset_key]["file:size"] = entry["size"]
            assets[asset_key]["file:checksum"] = entry["checksum"]
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT, PROJ_EXT, TABLE_EXT, FILE_EXT,
                            WEBMAP_EXT, PARTITION_EXT, STORAGE_EXT],
        "id": meta["collection"],
        "title": f"FTW Global — Field Boundaries {year} (GeoParquet)",
        "description": (
            f"Predicted agricultural field boundaries for {year}: "
            f"{n:,} parcels ({area:,.0f} km² of field area) in "
            f"{len(rows)} per-UTM-zone GeoParquet files, {gib:,.1f} GiB "
            f"total. Explore the year on the "
            f"[interactive map]({viewer(year)}), walk its metadata in the "
            f"[Portolan browser]({browser(f'vector/{year}/collection.json')}), "
            f"or download the files from "
            f"[Source Cooperative]({SOURCE_COOP}). {_PROJECT}\n\n"
            f"**How this was made.** {fix_details(meta['determination:details'])} "
            f"Source imagery: the "
            f"[TGE Labs Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}) "
            f"(CDSE mirror). The full pipeline, from mosaic download to this "
            f"file, is documented in "
            f"[pipeline/README.md]({REPO_URL}/blob/main/pipeline/README.md)."
            f"\n\nThe schema follows {_FIBOA} and {_VECOREL}: "
            f"columns {_COLS_SHORT} — see `table:columns` for definitions."
            f"\n\n**Reading all {len(rows)} partitions at once.** The glob is "
            f"`{s3_glob}` (`partition:glob`), anonymous read; pass "
            "`hive_partitioning=1` to get `zone` as a column. Expanding a "
            "wildcard needs a bucket listing, which plain HTTPS does not "
            "offer, so the `s3://` form is the one to paste into DuckDB, "
            "GDAL or fsspec — see `storage:schemes` for the endpoint and the "
            "path-style note. One partition at a time is a plain HTTPS read "
            f"of `{PUBLIC_BASE}/vector/{year}/zone=NN/utmNN.parquet`, and "
            "each partition is an item of this collection with its own size "
            "and checksum."
        ),
        "license": "CC-BY-4.0",
        "keywords": ["agriculture", "field boundaries", "Fields of the World",
                     "FTW", "global", "Sentinel-2", "GeoParquet", str(year)],
        "providers": PROVIDERS,
        "extent": {
            "spatial": {"bbox": [bbox]},
            "temporal": {"interval": [[f"{year}-01-01T00:00:00Z",
                                       f"{year}-12-31T23:59:59Z"]]},
        },
        "summaries": {"proj:code": ["EPSG:4326"]},
        "table:columns": TABLE_COLUMNS,
        "partition:scheme": "hive",
        "partition:keys": [{"name": "zone", "type": "string"}],
        "partition:glob": s3_glob,
        "partition:file_count": len(rows),
        "storage:schemes": STORAGE_SCHEMES,
        "portolan:styles": ([f"styles/{n}" for n in specs]
                            if has_tiles else []),
        "links": links,
        "assets": assets,
    }


def build_vector_catalog(per_year: dict[int, dict], out: Path) -> dict:
    children = []
    for year in sorted(per_year):
        children.append({
            "rel": "child", "href": f"./{year}/collection.json",
            "type": "application/json",
            "title": f"FTW Global — Field Boundaries {year} (GeoParquet)",
        })
    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT],
        "id": "vector",
        "title": "FTW Global (2nd Edition) — Vector field boundaries",
        "description": (
            "Per-year collections of predicted agricultural field boundaries "
            "as per-UTM-zone cloud-native GeoParquet, 2017–2025. Explore "
            f"them on the [interactive map]({VIEWER_URL}) or in the "
            f"[Portolan browser]({browser('vector/catalog.json')}); the "
            f"files are listed on [Source Cooperative]({SOURCE_COOP}). "
            f"{_PROJECT}"
        ),
        "links": [
            {"rel": "root", "href": "../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json"},
            {"rel": "vcs", "href": REPO_URL, "type": "text/html",
             "title": "Catalog source repository (metadata and pipeline)"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Vector tree README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Vector tree agent guide"},
            *children,
        ],
    }


# ── documentation ────────────────────────────────────────────────────────────

def _query_block(year: int) -> list[str]:
    url = f"{PUBLIC_BASE}/vector/{year}/zone=31/utm31.parquet"
    return [
        "```python",
        "import duckdb",
        "con = duckdb.connect()",
        'con.execute("INSTALL spatial; LOAD spatial; '
        'INSTALL httpfs; LOAD httpfs;")',
        f'url = "{url}"',
        "con.sql(f\"\"\"",
        "    SELECT count(*) AS parcels,",
        "           round(sum(\"metrics:area\") / 1e6, 1) AS km2,",
        "           round(avg(score), 1) AS avg_score",
        "    FROM read_parquet('{url}')",
        "\"\"\").show()",
        "```",
    ]


def year_readme(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    gib = sum(r["size_bytes"] for r in rows) / 2**30
    biggest = max(rows, key=lambda r: r["n_parcels"])
    browser_year = browser(f"vector/{year}/collection.json")
    lines = [
        f"# FTW Global — Field Boundaries {year} (GeoParquet)", "",
        f"Predicted agricultural field boundaries for {year}: **{n:,} "
        f"parcels** in {len(rows)} per-UTM-zone GeoParquet files "
        f"({gib:,.1f} GiB). {_PROJECT}", "",
        f"**[Open {year} on the interactive map]({viewer(year)})** to see the "
        f"fields over imagery, or **[open it in the Portolan browser]"
        f"({browser_year})** to walk the metadata and preview each asset. "
        f"The files themselves are listed on "
        f"[Source Cooperative]({SOURCE_COOP}).", "",
        f"Agents: [AGENTS.md]({doc(f'vector/{year}/AGENTS.md')}) beside "
        f"this file is the agent guide.", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## How it was made", "",
        f"{fix_details(meta['determination:details'])} Source imagery: the "
        f"[TGE Labs Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}). "
        f"[pipeline/README.md]({REPO_URL}/blob/main/pipeline/README.md) "
        f"documents every stage, from mosaic download to this file.",
        "",
        "## Files", "",
        f"One file per UTM zone at `vector/{year}/zone=NN/utm{{NN}}.parquet`, "
        "hive-partitioned by `zone`. The largest is "
        f"[utm{biggest['zone']:02d}]"
        f"({PUBLIC_BASE}/vector/{year}/zone={biggest['zone']:02d}"
        f"/{zone_stem(biggest['zone'])}.parquet), with "
        f"{biggest['n_parcels']:,} parcels. Zone numbers with no land "
        "coverage are absent.", "",
        "## Columns", "",
        f"The schema follows {_FIBOA} and {_VECOREL} "
        "(also machine-readable in each item's `table:columns`):", "",
        "| Column | Description |", "|---|---|",
        *[f"| `{c['name']}` | {c['description']} |" for c in TABLE_COLUMNS],
        "",
        "## Browse it", "",
        f"One [PMTiles archive]({PUBLIC_BASE}/vector/{year}/fields-{year}.pmtiles) "
        "renders the whole year with a zoom handover: A5 r7 cell "
        "aggregates (`cells` layer, z0–8: `count`, `area_ha`, `avg_score`, "
        "`pct_covered`) switching to the full field polygons (`fields` "
        "layer, z9–13: `id`, `metrics:area`, `metrics:perimeter`, "
        "`score`). Four styles — count, coverage (default), avg-size, "
        "field-prob — live beside it in `styles/`; the per-cell "
        f"aggregates are also published as "
        f"[GeoParquet]({PUBLIC_BASE}/vector/{year}/cells_a5r7_{year}.parquet).",
        "",
        "## Query it", "",
        *_query_block(year), "",
        "Read the whole year at once by globbing the partitions over `s3://` "
        "with `hive_partitioning=1`; an HTTP URL cannot expand a wildcard. "
        "The collection's `partition:glob` carries that glob, and its "
        "`storage:schemes` names the endpoint and the path-style setting the "
        "dotted bucket name needs.", "",
        "Coverage is not global. Only MGRS tiles with at least 1% cropland "
        "were processed, so a region below that threshold has no parcels "
        "here and an absence is not a prediction of absence. "
        f"{filter_note(meta)} "
        f"{nodata_note(year) + ' ' if nodata_note(year) else ''}"
        "Filter on `score` "
        "(the model's field probability × 100) to trade precision against "
        "recall.", "",
    ]
    return "\n".join(lines)


def year_agents(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    glob = (f"s3://us-west-2.opendata.source.coop/ftw/global-data-2e/"
            f"vector/{year}/zone=*/utm*.parquet")
    lines = [
        f"# AGENTS.md — FTW field boundaries {year}", "",
        "Guidance for AI agents. Every claim here is quoted from the "
        "dataset's embedded metadata or measured from the data.", "",
        f"- {n:,} parcels in {len(rows)} per-UTM-zone GeoParquet files at "
        f"`{PUBLIC_BASE}/vector/{year}/zone=NN/utm{{NN}}.parquet` "
        "(anonymous read, hive-partitioned by `zone`).",
        "- Whole-year queries glob the partitions over s3 with "
        "anonymous access and `hive_partitioning=1` (http URLs cannot "
        "glob):",
        "  ```python",
        "  import duckdb",
        "  con = duckdb.connect()",
        '  con.execute("INSTALL httpfs; LOAD httpfs; '
        "CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2', URL_STYLE 'path');\")",
        f"  con.sql(\"SELECT zone, count(*) FROM read_parquet('{glob}', "
        "hive_partitioning=1) GROUP BY zone ORDER BY zone\").show()",
        "  ```",
        f"- Schema: {len(TABLE_COLUMNS)} columns ({_COLS_SHORT}); "
        "definitions live in `table:columns` on the collection and every "
        "item.",
        "- Parcel ids are unique across the year (measured: no duplicate "
        "ids). A field that straddles a UTM zone line can appear once in "
        "each zone file; the post-processing QA counted about 2,000 to "
        "2,300 such overlapping pairs per year.",
        "- `metrics:area` is m². Post-processing kept parcels between "
        f"900 m² and 5 km². {filter_note(meta)}",
        *([f"- {nodata_note(year)}"] if nodata_note(year) else []),
        "- Coverage is cropland-gated: only MGRS tiles with at least 1% "
        "cropland were processed. Treat an empty region as unprocessed, not "
        "as a prediction that no fields exist there.",
        "- Query with DuckDB over https:// URLs (s3:// hangs on some "
        "networks); a browser-like User-Agent is needed for bucket "
        "listings only, not file reads.",
        "- The `items.parquet` collection mirror holds all item metadata "
        "for bulk spatial lookup of zones.", "",
        "Runnable example:", "",
        *_query_block(year), "",
    ]
    return "\n".join(lines)



def vector_readme(per_year: dict[int, list[dict]]) -> str:
    total = sum(r["n_parcels"] for rows in per_year.values() for r in rows)
    years = ", ".join(str(y) for y in sorted(per_year))
    return "\n".join([
        "# FTW Global (2nd Edition) — Vector field boundaries", "",
        f"Per-year collections of predicted agricultural field boundaries "
        f"({years}): **{total:,} parcels** total, as per-UTM-zone "
        f"cloud-native GeoParquet. {_PROJECT}", "",
        f"**[Open the interactive map]({VIEWER_URL})** to explore the fields "
        f"over imagery, or **[open the catalog in the Portolan browser]"
        f"({browser('vector/catalog.json')})** to walk the metadata. The "
        f"files are listed on [Source Cooperative]({SOURCE_COOP}).", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## Collections", "",
        "| Year | Parcels | Files | Browse |",
        "|---|---|---|---|",
        *[f"| {y} "
          f"| {sum(r['n_parcels'] for r in per_year[y]):,} "
          f"| [{len(per_year[y])} UTM zones]({folder(f'vector/{y}')}) "
          f"| [map]({viewer(y)}) · "
          f"[browser]({browser(f'vector/{y}/collection.json')}) |"
          for y in sorted(per_year)], "",
        "Each year is an independent prediction, so a parcel `id` carries no "
        "meaning across years; comparing years needs a spatial join.", "",
        f"The pipeline that built these files is documented in "
        f"[pipeline/README.md]({REPO_URL}/blob/main/pipeline/README.md), and "
        f"the catalog itself is maintained at [{REPO_URL.split('//')[1]}]"
        f"({REPO_URL}).", "",
    ])


def vector_agents(per_year: dict[int, list[dict]]) -> str:
    return "\n".join([
        "# AGENTS.md — FTW vector tree", "",
        "Guidance for AI agents. Every claim here is quoted from a source "
        "or measured from the data.", "",
        "- One collection per year: " + ", ".join(
            f"`{y}/collection.json`" for y in sorted(per_year)) + ".",
        "- Each collection documents its schema in `table:columns` and its "
        "own AGENTS.md; read those before querying.",
        "- Data layout: `vector/{year}/zone=NN/utm{NN}.parquet` — hive-"
        "partitioned by zone, each parquet colocated with its item "
        "metadata. A whole-year read globs "
        "`s3://us-west-2.opendata.source.coop/ftw/global-data-2e/"
        "vector/{year}/zone=*/utm*.parquet` with `hive_partitioning=1`; "
        "the https form cannot expand a wildcard.",
        "- Coverage is cropland-gated: only MGRS tiles with at least 1% "
        "cropland were processed, so an empty region is unprocessed rather "
        "than predicted empty. Inside a processed tile nothing is filtered "
        "by land cover, water or slope, so non-agricultural ground can "
        "carry parcels. The only size bounds are a 900 m² floor and a "
        "5 km² cap.",
        f"- Pipeline and catalog source: {REPO_URL}", "",
    ])



def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def patch_local_assets(year_dir: Path, collection: dict) -> None:
    """Fill file:size/file:checksum on assets whose files exist locally.

    Covers the stac-geoparquet mirror and the thumbnail. An absent file gets
    a note, not an invented value; generate it and re-run this script.
    """
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
        digest = hashlib.sha256()
        with local.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        asset["file:size"] = local.stat().st_size
        asset["file:checksum"] = "1220" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checksums", type=Path,
        help="JSON sidecar from tools/hash_remote.py (url -> size/checksum)",
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "catalog" / "vector",
    )
    args = parser.parse_args()

    con = connect()
    index = read_index(con)
    per_year: dict[int, list[dict]] = {}
    for row in index:
        per_year.setdefault(row["year"], []).append(row)
    print(f"index years: {sorted(per_year)}")

    # The committed items first, then the sidecar over the top: a sidecar is
    # written from the bytes that are about to be published, so it wins.
    checksums = committed_item_checksums(args.out, index)
    if args.checksums:
        checksums |= json.loads(args.checksums.read_text())

    missing = [r["href"] for r in index if r["href"] not in checksums]
    if args.checksums and missing:
        print(f"note: {len(missing)} file(s) have no checksum yet")

    for year, rows in sorted(per_year.items()):
        meta = fetch_year_meta(con, year, rows[0]["href"])
        year_dir = args.out / str(year)
        for row in rows:
            stem = zone_stem(row["zone"])
            write_json(year_dir / f"zone={row['zone']:02d}" / f"{stem}.json",
                       build_item(row, meta, checksums))
        tiles_meta = tiles_meta_for(year, args.out)
        if f"pmtiles_{year}" in tiles_meta:
            for name, spec in style_specs().items():
                write_json(year_dir / "styles" / f"{name}.json",
                           build_style(year, name, spec))
        collection = build_collection(year, rows, meta)
        patch_local_assets(year_dir, collection)
        write_json(year_dir / "collection.json", collection)
        (year_dir / "README.md").write_text(year_readme(year, rows, meta))
        (year_dir / "AGENTS.md").write_text(year_agents(year, rows, meta))
        print(f"{year}: {len(rows)} items, "
              f"{sum(r['n_parcels'] for r in rows):,} parcels")

    write_json(args.out / "catalog.json",
               build_vector_catalog(per_year, args.out))
    (args.out / "README.md").write_text(vector_readme(per_year))
    (args.out / "AGENTS.md").write_text(vector_agents(per_year))
    print(f"OK -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
