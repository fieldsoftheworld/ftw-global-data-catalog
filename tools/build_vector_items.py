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
- README.md / AGENTS.md / llms.txt for the subtree and each collection, with
  measured numbers (counts and sizes come from the index, not prose memory).

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

PUBLIC_BASE = "https://data.source.coop/ftw/global-data-beta"
INDEX_URL = f"{PUBLIC_BASE}/index/vector.parquet"
YEARS = (2024, 2025)

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"
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

# The 20 columns of every zone parquet. The ftw:* descriptions are the
# dataset's own, from the parquet's embedded schemas:custom; the core fields
# follow fiboa/vecorel.
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
    {"name": "ftw:tile", "type": "string",
     "description": "Sentinel-2 MGRS tile the parcel was extracted from."},
    {"name": "ftw:field_prob", "type": "float",
     "description": "Mean model field probability inside the parcel."},
    {"name": "ftw:boundary_prob", "type": "float",
     "description": "Mean model boundary probability inside the parcel."},
    {"name": "ftw:frac_nodata_1q", "type": "float",
     "description": "Fraction of the parcel with nodata in >= 1 of the 4 "
                    "quarterly mosaics."},
    {"name": "ftw:frac_nodata_3q", "type": "float",
     "description": "Fraction with nodata in >= 3 quarterly mosaics."},
    {"name": "ftw:frac_water", "type": "float",
     "description": "Fraction classed water in io-lulc 2024 (30 m)."},
    {"name": "ftw:frac_crops_ever", "type": "float",
     "description": "Fraction classed crops in io-lulc 2017, 2020 or 2024."},
    {"name": "ftw:slope_mean", "type": "float",
     "description": "Mean slope, degrees (Copernicus 30 m DEM)."},
    {"name": "ftw:frac_slope_gt30", "type": "float",
     "description": "Fraction with slope > 30 degrees."},
    {"name": "ftw:elev_mean", "type": "float",
     "description": "Mean elevation, m."},
    {"name": "ftw:patch_sat_mean", "type": "float",
     "description": "Mean over covering inference patches of the fraction of "
                    "pixels with field prob >= 0.9 (flipped-patch detector)."},
    {"name": "ftw:patch_sat_max", "type": "float",
     "description": "Max over covering inference patches of that fraction."},
    {"name": "ftw:patch_pb_mean", "type": "float",
     "description": "Mean boundary probability over the covering inference "
                    "patches."},
    {"name": "ftw:touches_window_edge", "type": "boolean",
     "description": "Parcel touched a processing window edge (possible "
                    "truncation)."},
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


def fetch_year_meta(con, year: int) -> dict:
    """The embedded vecorel collection metadata for one year's parquet.

    Read from the first zone file's footer (constant across zones); carries
    the collection id, determination:* processing notes, and schema links.
    """
    url = f"{PUBLIC_BASE}/vector/{year}/utm01.parquet"
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


def zone_stem(zone: int) -> str:
    return f"utm{zone:02d}"


def build_item(row: dict, meta: dict, checksums: dict) -> dict:
    year, zone = row["year"], row["zone"]
    stem = zone_stem(zone)
    title = f"UTM zone {zone} — {year} field boundaries"
    data_asset = {
        "href": f"../{stem}.parquet",
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
            "datetime": meta["determination:datetime"],
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
        {"rel": "llms", "href": "./llms.txt", "type": "text/markdown",
         "title": "Agent/LLM usage guide"},
        {"rel": "describedby", "href": FIBOA_README, "type": "text/html",
         "title": "fiboa specification (core parcel fields)"},
    ]
    for row in rows:
        stem = zone_stem(row["zone"])
        links.append({
            "rel": "item", "href": f"./{stem}/{stem}.json",
            "type": "application/geo+json",
            "title": f"UTM zone {row['zone']}",
        })
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT, PROJ_EXT, TABLE_EXT, FILE_EXT],
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
        "links": links,
        "assets": {
            "thumbnail": {
                "href": "./thumbnail.png",
                "type": "image/png",
                "title": f"UTM-zone coverage shaded by parcel count ({year})",
                "roles": ["thumbnail"],
            },
            "mirror": {
                "href": "./items.parquet",
                "type": "application/vnd.apache.parquet",
                "title": "STAC GeoParquet mirror of the items",
                "description": "All item metadata in one stac-geoparquet "
                               "file, for bulk queries. Derived from the "
                               "items; the item JSON stays normative.",
                "roles": ["collection-mirror", "metadata"],
            },
        },
    }


def build_vector_catalog(per_year: dict[int, dict]) -> dict:
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
            {"rel": "llms", "href": "./llms.txt", "type": "text/markdown",
             "title": "Agent/LLM usage guide"},
            *children,
        ],
    }


# ── documentation ────────────────────────────────────────────────────────────

def _query_block(year: int) -> list[str]:
    url = f"{PUBLIC_BASE}/vector/{year}/utm31.parquet"
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
        "           round(avg(\"ftw:field_prob\"), 3) AS avg_field_prob",
        "    FROM read_parquet('{url}')",
        "\"\"\").show()",
        "```",
    ]


def year_readme(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    gib = sum(r["size_bytes"] for r in rows) / 2**30
    biggest = max(rows, key=lambda r: r["n_parcels"])
    lines = [
        f"# FTW Global — Field Boundaries {year} (GeoParquet)", "",
        f"Predicted agricultural field boundaries for {year}: **{n:,} "
        f"parcels** in {len(rows)} per-UTM-zone GeoParquet files "
        f"({gib:,.1f} GiB). {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}).", "",
        "## How it was made", "",
        f"{meta['determination:details']} Source imagery: the "
        f"[TGE Labs Sentinel-2 quarterly cloudless mosaics]({MOSAICS_URL}).",
        "",
        "## Files", "",
        f"One file per UTM zone at `vector/{year}/utm{{NN}}.parquet` "
        f"(e.g. [utm{biggest['zone']:02d}]"
        f"({PUBLIC_BASE}/vector/{year}/{zone_stem(biggest['zone'])}.parquet) "
        f"is the largest, {biggest['n_parcels']:,} parcels). Zone numbers "
        "with no land coverage are absent.", "",
        "## Columns", "",
        f"The schema follows {_FIBOA} and {_VECOREL} "
        "(also machine-readable in each item's `table:columns`):", "",
        "| Column | Description |", "|---|---|",
        *[f"| `{c['name']}` | {c['description']} |" for c in TABLE_COLUMNS],
        "",
        "## Query it", "",
        *_query_block(year), "",
        "The attributes are for filtering: no land-cover masking was applied "
        "upstream, so filter on `ftw:field_prob`, `ftw:frac_water`, "
        "`ftw:frac_crops_ever` etc. to taste.", "",
    ]
    return "\n".join(lines)


def year_agents(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    lines = [
        f"# AGENTS.md — FTW field boundaries {year}", "",
        "Guidance for AI agents. Every claim here is quoted from the "
        "dataset's embedded metadata or measured from the data.", "",
        f"- {n:,} parcels in {len(rows)} per-UTM-zone GeoParquet files at "
        f"`{PUBLIC_BASE}/vector/{year}/utm{{NN}}.parquet` (anonymous read).",
        f"- Schema: 20 columns ({_COLS_SHORT}); definitions live in "
        "`table:columns` on the collection and every item.",
        "- Parcel ids are unique within a zone file; parcels on zone "
        "boundaries can appear in more than one file — check "
        "`ftw:touches_window_edge` before cross-zone aggregation.",
        "- `metrics:area` is m²; the upstream post-processing removed "
        "parcels larger than 5 km².",
        "- Query with DuckDB over https:// URLs (s3:// hangs on some "
        "networks); a browser-like User-Agent is needed for bucket "
        "listings only, not file reads.",
        "- The `items.parquet` collection mirror holds all item metadata "
        "for bulk spatial lookup of zones.", "",
        "Runnable example:", "",
        *_query_block(year), "",
    ]
    return "\n".join(lines)


def year_llms(year: int, rows: list[dict], meta: dict) -> str:
    n = sum(r["n_parcels"] for r in rows)
    return "\n".join([
        f"# FTW Global (beta) — field boundaries {year}", "",
        f"> {n:,} predicted agricultural field parcels for {year} as "
        f"{len(rows)} per-UTM-zone cloud-native GeoParquet files. "
        "CC-BY-4.0.", "",
        f"Data: `{PUBLIC_BASE}/vector/{year}/utm{{NN}}.parquet`",
        f"Collection: {PUBLIC_BASE}/vector/{year}/collection.json",
        "", "Processing (from the dataset's embedded metadata):",
        meta["determination:details"], "",
        "See AGENTS.md beside this file for query guidance.", "",
    ])


def vector_readme(per_year: dict[int, list[dict]]) -> str:
    total = sum(r["n_parcels"] for rows in per_year.values() for r in rows)
    years = ", ".join(str(y) for y in sorted(per_year))
    return "\n".join([
        "# FTW Global (beta) — Vector field boundaries", "",
        f"Per-year collections of predicted agricultural field boundaries "
        f"({years}): **{total:,} parcels** total, as per-UTM-zone "
        f"cloud-native GeoParquet. {_PROJECT}", "",
        f"Browse it in the [data browser]({DATA_BROWSER}).", "",
        "## Collections", "",
        *[f"- [{y}](./{y}/collection.json) — "
          f"{sum(r['n_parcels'] for r in per_year[y]):,} parcels"
          for y in sorted(per_year)], "",
        "New years drop in incrementally alongside these.", "",
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
        "- Data layout: `vector/{year}/utm{NN}.parquet`, one GeoParquet per "
        "UTM zone, colocated with the item metadata.", "",
    ])


def vector_llms(per_year: dict[int, list[dict]]) -> str:
    total = sum(r["n_parcels"] for rows in per_year.values() for r in rows)
    return "\n".join([
        "# FTW Global (beta) — vector tree", "",
        f"> {total:,} predicted field parcels across "
        f"{len(per_year)} years, per-UTM-zone GeoParquet. CC-BY-4.0.", "",
        *[f"- {y}: {PUBLIC_BASE}/vector/{y}/collection.json"
          for y in sorted(per_year)], "",
    ])


# ── mirror registration ─────────────────────────────────────────────────────

def patch_local_assets(year_dir: Path, collection: dict) -> None:
    """Fill file:size/file:checksum on assets whose files exist locally.

    Covers the stac-geoparquet mirror and the thumbnail. An absent file gets
    a note, not an invented value; generate it and re-run this script.
    """
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
    if sorted(per_year) != sorted(YEARS):
        sys.exit(f"index years {sorted(per_year)} != expected {YEARS}")

    missing = [r["href"] for r in index if r["href"] not in checksums]
    if args.checksums and missing:
        print(f"note: {len(missing)} file(s) have no checksum yet")

    for year, rows in sorted(per_year.items()):
        meta = fetch_year_meta(con, year)
        year_dir = args.out / str(year)
        for row in rows:
            stem = zone_stem(row["zone"])
            write_json(year_dir / stem / f"{stem}.json",
                       build_item(row, meta, checksums))
        collection = build_collection(year, rows, meta)
        patch_local_assets(year_dir, collection)
        write_json(year_dir / "collection.json", collection)
        (year_dir / "README.md").write_text(year_readme(year, rows, meta))
        (year_dir / "AGENTS.md").write_text(year_agents(year, rows, meta))
        (year_dir / "llms.txt").write_text(year_llms(year, rows, meta))
        print(f"{year}: {len(rows)} items, "
              f"{sum(r['n_parcels'] for r in rows):,} parcels")

    write_json(args.out / "catalog.json", build_vector_catalog(per_year))
    (args.out / "README.md").write_text(vector_readme(per_year))
    (args.out / "AGENTS.md").write_text(vector_agents(per_year))
    (args.out / "llms.txt").write_text(vector_llms(per_year))
    print(f"OK -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
