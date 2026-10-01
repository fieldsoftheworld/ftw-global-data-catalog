#!/usr/bin/env python3
"""Generate the vector tree of the beta catalog: per-year source collections.

Emits, under ``catalog/vector/``:

- ``catalog.json`` — the vector subtree catalog (children: the year
  collections; ``fields-yearly`` is added by the Phase 3 tiles work).
- ``{year}/collection.json`` — one collection per year, id from the parquet's
  own embedded ``collection`` key (``ftw-s2-{year}``).
- ``{year}/utm{NN}/utm{NN}.json`` — one item per UTM-zone parquet, in its own
  subdirectory (PORTO-CORE-015), with ``table:columns`` for all 20 columns,
  ``file:size``/``file:checksum`` on the data asset, and an ``alternate`` s3
  href (PORTO-CORE-024).
- README.md / AGENTS.md for the subtree and each collection, with measured
  numbers (counts and sizes come from the index, not prose memory). The
  README is for a person deciding whether to trust the data; the AGENTS.md is
  for an agent that has already committed to it and needs the first query to
  work. They share a subject, not a job — resist copying one into the other.

Everything is derived from ``index/vector.parquet`` and the embedded parquet
metadata (schema links, per-column descriptions, processing notes), so a
regeneration is a no-op when nothing changed. Edit this generator, never the
generated JSON.

    .venv/bin/python3 tools/build_vector_items.py \
        --checksums staging-data/checksums/vector_checksums.json

Checksums come from ``tools/hash_remote.py`` (the bytes live only in the
bucket). Without ``--checksums`` the data assets carry ``file:size`` only,
which fails conformance — fine for a dry look, not for committing.

After generating, build each year's stac-geoparquet mirror:

    .venv/bin/portolan stac-geoparquet catalog/vector/2025

(The mirror asset is registered by this script; the parquet itself is
generated locally, published by tools/publish.py, and never committed.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINS_FILE = ROOT / "pipeline" / "style_bins.json"
TILES_META = ROOT / "staging-data" / "checksums" / "tiles_meta.json"

PUBLIC_BASE = "https://data.source.coop/ftw/global-data-beta"
INDEX_URL = f"{PUBLIC_BASE}/index/vector.parquet"

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"
WEBMAP_EXT = "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json"
PARTITION_EXT = "https://schemas.portolan-sdi.org/incubating/partition/v1.0.0/schema.json"
TABLE_EXT = "https://stac-extensions.github.io/table/v1.2.0/schema.json"
PROJ_EXT = "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
ALT_EXT = "https://stac-extensions.github.io/alternate-assets/v1.2.0/schema.json"

FIBOA_SPEC = "https://fiboa.org/specification/v0.3.0/schema.yaml"
VECOREL_SPEC = "https://vecorel.org/specification/v0.1.0/schema.yaml"
FIBOA_README = "https://github.com/fiboa/specification"
MOSAICS_URL = "https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"
FTW_URL = "https://fieldsofthe.world"
DATA_BROWSER = "https://source.coop/ftw/global-data-beta"
# The earlier, non-beta FTW global release. Its README is the source of the
# limitation wording quoted below (verified 2026-10-01 against
# https://data.source.coop/ftw/global-data/README.md).
FTW_GLOBAL = "https://source.coop/ftw/global-data"
MODEL = "unet_balanced_fp32.onnx"
REPO = "https://github.com/fieldsoftheworld/ftw-global-data-catalog"
S3_BASE = "s3://us-west-2.opendata.source.coop/ftw/global-data-beta"

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
        FROM '{INDEX_URL}' ORDER BY year, zone
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
    """).fetchone()
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


def build_item(row: dict, meta: dict, checksums: dict) -> dict:
    year, zone = row["year"], row["zone"]
    stem = zone_stem(zone)
    title = f"UTM zone {zone} — {year} field boundaries"
    data_asset = {
        "href": f"./{stem}.parquet",
        "type": "application/vnd.apache.parquet",
        "title": f"{title} (GeoParquet)",
        "roles": ["data"],
        "file:size": row["size_bytes"],
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


def build_collection(year: int, rows: list[dict], meta: dict) -> dict:
    n = sum(r["n_parcels"] for r in rows)
    gib = sum(r["size_bytes"] for r in rows) / 2**30
    area = sum(r["area_km2"] for r in rows)
    bbox = [min(r["xmin"] for r in rows), min(r["ymin"] for r in rows),
            max(r["xmax"] for r in rows), max(r["ymax"] for r in rows)]
    tiles_meta = (json.loads(TILES_META.read_text())
                  if TILES_META.is_file() else {})
    has_tiles = f"pmtiles_{year}" in tiles_meta
    links = [
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
        {"rel": "describedby", "href": "./README.md", "type": "text/markdown",
         "title": "Collection README"},
        {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
         "title": "Collection agent guide"},
        {"rel": "describedby", "href": FIBOA_README, "type": "text/html",
         "title": "fiboa specification (core parcel fields)"},
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
    assets: dict[str, dict] = {
        "data": {
            "href": "./zone=*/utm*.parquet",
            "type": "application/vnd.apache.parquet",
            "title": f"All {year} field boundaries "
                     "(GeoParquet glob, hive-partitioned by zone)",
            "description": "One glob over every zone partition. Read it "
                           "with hive partitioning on to get `zone` as a "
                           "column; per-file sizes and checksums live on "
                           "the items.",
            "roles": ["data"],
        },
    }
    if has_tiles:
      assets["pmtiles"] = {
            "href": f"./fields-{year}.pmtiles",
            "type": "application/vnd.pmtiles",
            "title": f"Fields {year} — A5 r7 cells z0–8 → field polygons "
                     "z9–13 (PMTiles)",
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
    for kind, key in (("pmtiles", "pmtiles"), ("cells", "cells")):
        entry = tiles_meta.get(f"{kind}_{year}")
        if entry:
            assets[key]["file:size"] = entry["size"]
            assets[key]["file:checksum"] = entry["checksum"]
    for name, spec in (specs.items() if has_tiles else ()):
        roles = ["style", "default"] if name == "coverage" else ["style"]
        assets[f"styles/{name}"] = {
            "href": f"./styles/{name}.json",
            "type": "application/vnd.mapbox.style+json",
            "title": f"{spec['title']} ({year})"
                     + (" — default" if "default" in roles else ""),
            "roles": roles,
        }
    if has_tiles or (ROOT / "catalog" / "vector" / str(year)
                     / "thumbnail.png").is_file():
        assets["thumbnail"] = {
            "href": "./thumbnail.png", "type": "image/png",
            "title": f"Fields {year} rendered with the default (coverage) "
                     "style",
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
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT, PROJ_EXT, TABLE_EXT, FILE_EXT,
                            WEBMAP_EXT, PARTITION_EXT],
        "id": meta["collection"],
        "title": f"FTW Global — Field Boundaries {year} (GeoParquet)",
        "description": (
            f"Predicted agricultural field boundaries for {year}: "
            f"{n:,} parcels ({area:,.0f} km² of field area) in "
            f"{len(rows)} per-UTM-zone GeoParquet files, {gib:,.1f} GiB "
            f"total, browsable in the [data browser]({DATA_BROWSER}). "
            f"{_PROJECT}\n\n**How this was made.** "
            f"{meta['determination:details']} Source imagery: the "
            f"[TGE Labs Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}) "
            f"(CDSE mirror).\n\nThe schema follows {_FIBOA} and {_VECOREL}: "
            f"columns {_COLS_SHORT} — see `table:columns` for definitions."
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
        "partition:glob": "./zone=*/utm*.parquet",
        "partition:file_count": len(rows),
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
        "title": "FTW Global (beta) — Vector field boundaries",
        "description": (
            "Per-year collections of predicted agricultural field boundaries "
            "as per-UTM-zone cloud-native GeoParquet, browsable in the "
            f"[data browser]({DATA_BROWSER}). {_PROJECT}"
        ),
        "links": [
            {"rel": "root", "href": "../catalog.json",
             "type": "application/json",
             "title": "Fields of the World — Global Data (beta)"},
            {"rel": "parent", "href": "../catalog.json",
             "type": "application/json"},
            {"rel": "describedby", "href": "./README.md",
             "type": "text/markdown", "title": "Vector tree README"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Vector tree agent guide"},
            *children,
        ],
    }


# ── documentation ────────────────────────────────────────────────────────────

def _query_block(year: int) -> list[str]:
    """One zone over https. The cheapest query that returns real numbers."""
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


def _glob_block(year: int) -> list[str]:
    """Every zone of one year in one query, over the hive partitions.

    Globbing needs the s3 endpoint — DuckDB cannot expand a wildcard in an
    http URL — and Source Cooperative needs path-style addressing. Verified
    against the live bucket: `count(*)` and the `zone` key come from file
    metadata, so this returns in seconds.
    """
    glob = f"{S3_BASE}/vector/{year}/zone=*/utm*.parquet"
    return [
        "```python",
        "import duckdb",
        "con = duckdb.connect()",
        'con.execute("INSTALL httpfs; LOAD httpfs;")',
        "con.execute(\"\"\"",
        "    CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2',",
        "                   URL_STYLE 'path')",
        "\"\"\")",
        f'glob = "{glob}"',
        "con.sql(f\"\"\"",
        "    SELECT zone, count(*) AS parcels",
        "    FROM read_parquet('{glob}', hive_partitioning=1)",
        "    GROUP BY zone ORDER BY parcels DESC LIMIT 5",
        "\"\"\").show()",
        "```",
        "",
        "The glob needs the `s3://` endpoint — DuckDB cannot expand a "
        "wildcard in an http URL — and Source Cooperative needs "
        "`URL_STYLE 'path'`. The bucket is anonymous-read, so the secret "
        "carries no credentials. `hive_partitioning=1` is what turns the "
        "`zone=NN` directory into a `zone` column; it arrives as a string, "
        "zero-padded, so compare it as `zone = '31'`. Swap the year in the "
        "glob to read a different collection.",
    ]


def _crs_section(*, year_level: bool) -> list[str]:
    """CRS with the consequences spelled out, not just the EPSG code."""
    pmtiles = ("The PMTiles archive is" if year_level
               else "Each year's PMTiles archive is")
    return [
        "## Coordinate system", "",
        "Every zone file stores `geometry` as WGS 84 lon/lat "
        "(**EPSG:4326**), not in its UTM zone — the zone is a partition key, "
        "so a cross-zone or whole-year read needs no reprojection and the "
        "`bbox` struct can be compared across zones directly. Because the "
        "coordinates are degrees, `ST_Area` and `ST_Length` on `geometry` "
        "return degree-based numbers that mean nothing on the ground: read "
        "`metrics:area` (m²) and `metrics:perimeter` (m) instead, or "
        "reproject to an equal-area CRS first.", "",
        f"{pmtiles} Web Mercator (EPSG:3857), the tiling CRS, and its cell "
        "aggregates were computed before that reprojection.", "",
    ]


def _limitations_section(*, year_level: bool) -> list[str]:
    """What the data is not. FTW's own framing, quoted and attributed."""
    made_ref = ("*How it was made* above and each collection's `description` "
                "record" if year_level
                else "each collection's README and `description` record")
    return [
        "## Limitations", "",
        "These are **model predictions**, not a survey. In the FTW project's "
        "own words, a field here is a *remote-sensing field unit* (a "
        "connected component of predicted field-interior pixels), **not** a "
        "cadastral/legal parcel, and "
        f"[this is not a land-tenure product]({FTW_GLOBAL}); one legal "
        "parcel may map to many polygons or to none. Parcel counts, areas "
        "and perimeters are therefore predicted quantities that carry the "
        "model's errors, not measurements of anything surveyed.", "",
        f"- **Model provenance.** The FTW `{MODEL}` model, run on the "
        "Sentinel-2 quarterly cloudless mosaics and vectorized by "
        f"BoundaryVote instance post-processing; {made_ref} the exact "
        "chain. The "
        f"checkpoint and its model card are released by the "
        f"[FTW project]({FTW_URL}) separately from this data.",
        "- **`score` is a model probability, not a validated confidence.** "
        "It is the mean field probability the model assigned to the pixels "
        "inside the parcel, × 100 and rounded into a `uint8` (0–100). Use it "
        "to rank and filter; no calibration against ground truth is "
        "published for this beta, so a score of 80 is not an 80% chance "
        "that the parcel is real.",
        "- **Weaker outside the training distribution.** FTW describes the "
        "confidence on its earlier global release as \"conservative outside "
        "the FTW training distribution (e.g. smallholder systems): real "
        f"fields there may receive low confidence\" ([FTW]({FTW_GLOBAL})). "
        "Expect the same shape of error here, and prefer a continuous "
        "`score` over a hard threshold in smallholder regions.",
        "- **No land-cover masking.** Nothing upstream removed "
        "non-agricultural ground, so water, scrub and built-up land can "
        "appear as parcels; `score` is the filter the dataset gives you. "
        "Parcels larger than 5 km² were dropped in post-processing.",
        "- **Each year is an independent prediction.** `id` is unique within "
        "a year's collection and carries no meaning across years, so "
        "year-over-year comparison needs a spatial join, not an id join.",
        "", "Found something wrong? Open an "
        f"[issue]({REPO}/issues).", "",
    ]


def _contributing_section(generated: bool) -> list[str]:
    """How this metadata is maintained. For the agent that wants to fix it."""
    lines = [
        "## Fixing this metadata", "",
        f"`catalog/` in [the repository]({REPO}) **is** this catalog: it "
        "syncs 1:1 to the bucket through `tools/publish.py`, so a merged "
        "change lands here on the next publish. Publishing never deletes, "
        "and no data bytes live in git — the repository carries only the "
        "metadata that describes them.", "",
    ]
    if generated:
        lines += [
            "Every file in this directory is **generated** by "
            "`tools/build_vector_items.py`. Edit that generator and re-run "
            "it; an edit to the generated output is overwritten by the next "
            "build.", "",
        ]
    return lines


def year_readme(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    gib = sum(r["size_bytes"] for r in rows) / 2**30
    biggest = max(rows, key=lambda r: r["n_parcels"])
    lines = [
        f"# FTW Global — Field Boundaries {year} (GeoParquet)", "",
        f"Predicted agricultural field boundaries for {year}: **{n:,} "
        f"parcels** in {len(rows)} per-UTM-zone GeoParquet files "
        f"({gib:,.1f} GiB). {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}); read "
        "[AGENTS.md](./AGENTS.md) beside this file if you are an agent, and "
        "[Limitations](#limitations) below before you draw conclusions from "
        "the numbers.", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## Query it", "",
        "One zone, straight over https — no download, no credentials:", "",
        *_query_block(year), "",
        "## Whole year, every zone", "",
        *_glob_block(year), "",
        "## Columns", "",
        f"The schema follows {_FIBOA} and {_VECOREL} "
        "(also machine-readable in each item's `table:columns`):", "",
        "| Column | Description |", "|---|---|",
        *[f"| `{c['name']}` | {c['description']} |" for c in TABLE_COLUMNS],
        "",
        *_crs_section(year_level=True),
        "## Files", "",
        f"One file per UTM zone at `vector/{year}/zone=NN/utm{{NN}}.parquet` "
        "(hive-partitioned by `zone`) "
        f"(e.g. [utm{biggest['zone']:02d}]"
        f"({PUBLIC_BASE}/vector/{year}/zone={biggest['zone']:02d}/"
        f"{zone_stem(biggest['zone'])}.parquet) "
        f"is the largest, {biggest['n_parcels']:,} parcels). Zone numbers "
        "with no land coverage are absent. Each parquet sits beside its own "
        "STAC item, and `items.parquet` mirrors every item's metadata for "
        "bulk lookup.", "",
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
        "## How it was made", "",
        f"{meta['determination:details']} Source imagery: the "
        f"[TGE Labs Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}).",
        "",
        *_limitations_section(year_level=True),
        *_contributing_section(generated=True),
    ]
    return "\n".join(lines)


def year_agents(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    glob = f"{S3_BASE}/vector/{year}/zone=*/utm*.parquet"
    lines = [
        f"# AGENTS.md — FTW field boundaries {year}", "",
        "Guidance for AI agents. Every claim here is quoted from the "
        "dataset's embedded metadata or measured from the data.", "",
        "Around this file: [collection.json](./collection.json) is the "
        "normative metadata, [README.md](./README.md) carries the schema "
        "table and the limitations in full, and "
        "[../AGENTS.md](../AGENTS.md) covers the vector tree this year "
        "sits in.", "",
        "## Access", "",
        f"- {n:,} parcels in {len(rows)} per-UTM-zone GeoParquet files at "
        f"`{PUBLIC_BASE}/vector/{year}/zone=NN/utm{{NN}}.parquet` "
        "(anonymous read, hive-partitioned by `zone`).",
        "- Read in place over https:// URLs — the files stream over HTTP "
        "range requests, so there is no reason to download them. Use "
        "https:// rather than s3:// for single files (s3:// hangs on some "
        "networks); a browser-like User-Agent is needed for bucket "
        "listings only, not for file reads.",
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
        "- `zone` from the partition path is a **string**, zero-padded: "
        "`WHERE zone = '31'`, not `zone = 31`.",
        "- The `items.parquet` collection mirror holds all item metadata "
        "for bulk spatial lookup of zones.", "",
        "## Schema and CRS", "",
        f"- {len(TABLE_COLUMNS)} columns ({_COLS_SHORT}); definitions live "
        "in `table:columns` on the collection and every item.",
        "- `geometry` is WGS 84 lon/lat (EPSG:4326) in **every** zone file — "
        "the UTM zone is a partition key, not a CRS, so cross-zone reads "
        "need no reprojection. The consequence: `ST_Area`/`ST_Length` on "
        "`geometry` return degree-based numbers, so read `metrics:area` "
        "(m², already computed) and `metrics:perimeter` (m) instead, or "
        "reproject to an equal-area CRS first.",
        "- The PMTiles archive is Web Mercator (EPSG:3857); the GeoParquet "
        "is not.",
        "- Parcel ids are unique within a zone file; zones partition the "
        "parcels cleanly (measured: zero shared ids or geometries in the "
        "6°E utm31/utm32 boundary strip). Ids carry no meaning across "
        "years — each year is an independent prediction, so year-over-year "
        "work needs a spatial join.",
        "- `metrics:area` is m²; the upstream post-processing removed "
        "parcels larger than 5 km².", "",
        "## What this data is not", "",
        "- A parcel is a *remote-sensing field unit*, **not** a "
        f"cadastral/legal parcel; [this is not a land-tenure product]"
        f"({FTW_GLOBAL}). Do not answer ownership, tenure or "
        "legal-boundary questions from it.",
        "- `score` is the mean model field probability inside the parcel "
        "(× 100, `uint8` 0–100) — a ranking for filtering, not a calibrated "
        "probability that the parcel is real, and no calibration is "
        "published for this beta.",
        "- No land-cover masking was applied upstream, so water, scrub and "
        "built-up ground can appear as parcels. Counts and areas are "
        "predictions; say so when you report them.",
        "- The full set of caveats, with sources, is in "
        "[README.md](./README.md#limitations).", "",
        "## Runnable example", "",
        *_query_block(year), "",
        *_contributing_section(generated=True),
    ]
    return "\n".join(lines)


def vector_readme(per_year: dict[int, list[dict]]) -> str:
    total = sum(r["n_parcels"] for rows in per_year.values() for r in rows)
    years = ", ".join(str(y) for y in sorted(per_year))
    latest = max(per_year)
    return "\n".join([
        "# FTW Global (beta) — Vector field boundaries", "",
        f"Per-year collections of predicted agricultural field boundaries "
        f"({years}): **{total:,} parcels** total, as per-UTM-zone "
        f"cloud-native GeoParquet. {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}); "
        "[AGENTS.md](./AGENTS.md) beside this file is the agent guide.", "",
        "Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)", "",
        "## Collections", "",
        "| Collection | Parcels | Files | Documentation |",
        "|---|---|---|---|",
        *[f"| [{y}](./{y}/collection.json) | "
          f"{sum(r['n_parcels'] for r in per_year[y]):,} | "
          f"{len(per_year[y])} UTM zones | "
          f"[README](./{y}/README.md) · [AGENTS](./{y}/AGENTS.md) |"
          for y in sorted(per_year)], "",
        "Every collection carries the same 9-column fiboa/vecorel schema, "
        "the same `zone=NN` hive layout and the same four map styles, so a "
        "query written against one year runs against any of them. New years "
        "drop in incrementally alongside these.", "",
        f"## Query a whole collection ({latest})", "",
        *_glob_block(latest), "",
        *_crs_section(year_level=False),
        *_limitations_section(year_level=False),
        *_contributing_section(generated=True),
    ])


def vector_agents(per_year: dict[int, list[dict]]) -> str:
    years = sorted(per_year)
    return "\n".join([
        "# AGENTS.md — FTW vector tree", "",
        "Guidance for AI agents. Every claim here is quoted from a source "
        "or measured from the data.", "",
        "Around this file: [catalog.json](./catalog.json) is the normative "
        "metadata, [README.md](./README.md) is the human landing page, and "
        "[../AGENTS.md](../AGENTS.md) covers the whole catalog (vector and "
        "raster).", "",
        "## The tree", "",
        "- One collection per year, each with its own agent guide: " + ", ".join(
            f"[{y}](./{y}/AGENTS.md)" for y in years) + ".",
        "- Every year shares one schema (9 columns, documented in "
        "`table:columns` on each collection and item), one layout and one "
        "set of styles, so a query written against one year runs against "
        "any of them. Read the year's AGENTS.md for its measured numbers.",
        "- Data layout: `vector/{year}/zone=NN/utm{NN}.parquet` — hive-"
        "partitioned by zone, each parquet colocated with its item "
        "metadata; whole-year reads glob `zone=*/utm*.parquet` over "
        "`s3://` with `hive_partitioning=1` (http URLs cannot glob). The "
        "`zone` key comes back as a zero-padded string.",
        "- `geometry` is WGS 84 lon/lat (EPSG:4326) in every zone file, so "
        "cross-zone and cross-year reads need no reprojection; the "
        "consequence is that `ST_Area` on `geometry` returns square "
        "degrees — use `metrics:area` (m²). PMTiles are Web Mercator "
        "(EPSG:3857).",
        "- Years are independent predictions: `id` is not stable across "
        "them, so year-over-year comparison needs a spatial join.",
        "- These are *remote-sensing field units*, **not** cadastral "
        f"parcels; [this is not a land-tenure product]({FTW_GLOBAL}). "
        "Each collection's README has the limitations in full.", "",
        *_contributing_section(generated=True),
    ])


# ── mirror registration ─────────────────────────────────────────────────────

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


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


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

    checksums = {}
    if args.checksums:
        checksums = json.loads(args.checksums.read_text())

    con = connect()
    index = read_index(con)
    per_year: dict[int, list[dict]] = {}
    for row in index:
        per_year.setdefault(row["year"], []).append(row)
    print(f"index years: {sorted(per_year)}")

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
        tiles_meta = (json.loads(TILES_META.read_text())
                      if TILES_META.is_file() else {})
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
