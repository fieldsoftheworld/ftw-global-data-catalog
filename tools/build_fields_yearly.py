#!/usr/bin/env python3
"""Generate the fields-yearly handover collection: metadata, styles, docs.

The Phase 3 deliverable is one PMTiles archive per year with a zoom
handover — a5 r7 ``cells`` layer z0–8, full ``fields`` polygons z9–13 —
staged to ``vector/fields-yearly/`` beside this collection's metadata. This
script emits ``catalog/vector/fields-yearly/``:

- ``collection.json`` — pmtiles assets (``pmtiles:layers: ["cells",
  "fields"]``), the cells GeoParquet, the styles (exactly one carries the
  ``default`` role, PORTO-CORE-070), file:size/checksum stamped from the
  staged data files when present.
- ``styles/{name}-{year}.json`` — MapLibre styles whose bins come from
  ``pipeline/style_bins.json``, the measured, checkpoint-approved bin edges.
  Every zoom-styled expression is a ``step``/``match`` so the browser can
  extract a legend.
- README.md / AGENTS.md / llms.txt.

    .venv/bin/python3 tools/build_fields_yearly.py --years 2025

Data files are staged at ``staging-data/vector/fields-yearly/`` (uploaded by
``tools/upload_data.py``); when present their file:size/checksum are
stamped, otherwise a note is printed. ``tools/build_vector_items.py`` links
the collection from the vector catalog once this directory exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "vector" / "fields-yearly"
STAGING = ROOT / "staging-data" / "vector" / "fields-yearly"
BINS_FILE = ROOT / "pipeline" / "style_bins.json"

PUBLIC_BASE = "https://data.source.coop/ftw/global-data-beta"
COLLECTION_URL = f"{PUBLIC_BASE}/vector/fields-yearly"
DATA_BROWSER = "https://source.coop/ftw/global-data-beta"
FTW_URL = "https://fieldsofthe.world"
MOSAICS_URL = "https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"

PORTOLAN_EXT = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"
WEBMAP_EXT = "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json"
FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"

GREEN = "#33a02c"

_PROJECT = (
    f"Part of [Fields of the World]({FTW_URL}) — agricultural field "
    "boundaries delineated from Sentinel-2 imagery."
)


def load_bins() -> dict:
    """The measured, checkpoint-approved bin edges and ramps per style."""
    return json.loads(BINS_FILE.read_text())


def step(prop_expr, colors: list[str], edges: list[float]) -> list:
    """A MapLibre step expression: len(colors) == len(edges) + 1."""
    assert len(colors) == len(edges) + 1, (colors, edges)
    out = ["step", prop_expr]
    out.append(colors[0])
    for edge, color in zip(edges, colors[1:]):
        out += [edge, color]
    return out


def fields_green(year: int) -> list[dict]:
    return [
        {"id": "fields-fill", "type": "fill", "source": "data",
         "source-layer": "fields", "minzoom": 9,
         "paint": {"fill-color": GREEN, "fill-opacity": 0.25}},
        {"id": "fields-outline", "type": "line", "source": "data",
         "source-layer": "fields", "minzoom": 9,
         "paint": {"line-color": GREEN, "line-width": 1}},
    ]


def build_style(year: int, name: str, spec: dict) -> dict:
    """One style: cells choropleth z0-8.x + fields z9+.

    spec: {"colors": [...], "edges": [...], "cell_prop": <expr>,
           "field_prop": <expr or None>, "title": ..., "description": ...}
    """
    url = f"pmtiles://{COLLECTION_URL}/fields-{year}.pmtiles"
    cells_layer = {
        "id": "cells-fill", "type": "fill", "source": "data",
        "source-layer": "cells", "maxzoom": 9,
        "paint": {
            "fill-color": step(spec["cell_prop"], spec["colors"],
                               spec["edges"]),
            "fill-opacity": 0.8,
        },
    }
    if spec.get("field_prop") is not None:
        field_layers = [
            {"id": "fields-fill", "type": "fill", "source": "data",
             "source-layer": "fields", "minzoom": 9,
             "paint": {
                 "fill-color": step(spec["field_prop"],
                                    spec["colors"],
                                    spec.get("field_edges", spec["edges"])),
                 "fill-opacity": 0.7,
             }},
        ]
    else:
        field_layers = fields_green(year)
    return {
        "version": 8,
        "name": f"{spec['title']} ({year})",
        "metadata": {"description": spec["description"].format(year=year)},
        "sources": {"data": {"type": "vector", "url": url}},
        "layers": [cells_layer, *field_layers],
    }


def style_specs(bins: dict) -> dict[str, dict]:
    return {
        "count": {
            "title": "Field count (A5 r7 → fields)",
            "description": "Fields per A5 r7 cell at z0–8 (stepped bins), "
                           "handing over to the actual {year} field "
                           "polygons from z9.",
            "cell_prop": ["get", "count"],
            "field_prop": None,
            **bins["count"],
        },
        "coverage": {
            "title": "Field coverage (A5 r7 → fields)",
            "description": "Percent of each A5 r7 cell covered by {year} "
                           "fields at z0–8, handing over to the actual "
                           "field polygons from z9.",
            "cell_prop": ["get", "pct_covered"],
            "field_prop": None,
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


def stamp_file_meta(asset: dict, local: Path) -> None:
    if not local.is_file():
        print(f"note: {local} absent; asset left without file:* "
              "(stage it and re-run)")
        return
    digest = hashlib.sha256()
    with local.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    asset["file:size"] = local.stat().st_size
    asset["file:checksum"] = "1220" + digest.hexdigest()


def build_collection(years: list[int], bins: dict, default: str) -> dict:
    specs = style_specs(bins)
    assets: dict[str, dict] = {}
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
    ]
    style_names = []
    for year in years:
        links.append({
            "rel": "pmtiles", "href": f"./fields-{year}.pmtiles",
            "type": "application/vnd.pmtiles",
            "title": f"Fields {year} (cells z0–8 → fields z9–13)",
            "pmtiles:layers": ["cells", "fields"],
        })
        assets[f"pmtiles_{year}"] = {
            "href": f"./fields-{year}.pmtiles",
            "type": "application/vnd.pmtiles",
            "title": f"Fields {year} — A5 r7 cells z0–8 → field polygons "
                     f"z9–13 (PMTiles)",
            "roles": ["visual"],
        }
        assets[f"cells_{year}"] = {
            "href": f"./cells_a5r7_{year}.parquet",
            "type": "application/vnd.apache.parquet",
            "title": f"A5 r7 cell aggregates {year} (GeoParquet)",
            "roles": ["data"],
        }
        for name in specs:
            key = f"styles/{name}-{year}"
            style_names.append(key)
            roles = ["style"]
            if name == default and year == max(years):
                roles.append("default")
            assets[key] = {
                "href": f"./styles/{name}-{year}.json",
                "type": "application/vnd.mapbox.style+json",
                "title": f"{specs[name]['title']} ({year})"
                         + (" — default" if "default" in roles else ""),
                "roles": roles,
            }
    assets["thumbnail"] = {
        "href": "./thumbnail.png", "type": "image/png",
        "title": "Fields rendered with the default style",
        "roles": ["thumbnail"],
    }
    year_span = f"{min(years)}" if len(years) == 1 else \
        f"{min(years)}–{max(years)}"
    return {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [PORTOLAN_EXT, WEBMAP_EXT, FILE_EXT],
        "id": "fields-yearly",
        "title": "FTW Global (beta) — Fields by year (PMTiles)",
        "description": (
            f"Browsable field-boundary tiles for {year_span}: one PMTiles "
            "archive per year with a zoom handover — A5 r7 cell aggregates "
            "(`cells` layer, z0–8: count, area_ha, avg_score, "
            "pct_covered) switching to the full field "
            "polygons with all attributes (`fields` layer, z9–13). Open it "
            f"in the [data browser]({DATA_BROWSER}). {_PROJECT}"
        ),
        "license": "CC-BY-4.0",
        "keywords": ["agriculture", "field boundaries", "Fields of the World",
                     "FTW", "global", "PMTiles", "visualization"],
        "providers": [
            {"name": "Taylor Geospatial",
             "roles": ["producer", "licensor", "processor", "host"],
             "url": "https://taylorgeospatial.org/"},
        ],
        "extent": {
            "spatial": {"bbox": [[-180.0, -60.0, 180.0, 84.0]]},
            "temporal": {"interval": [[f"{min(years)}-01-01T00:00:00Z",
                                       f"{max(years)}-12-31T23:59:59Z"]]},
        },
        "portolan:styles": style_names,
        "links": links,
        "assets": assets,
    }


def collection_readme(years: list[int], bins: dict) -> str:
    specs = style_specs(bins)
    return "\n".join([
        "# FTW Global (beta) — Fields by year (PMTiles)", "",
        f"Browsable field-boundary tiles for {', '.join(map(str, years))}. "
        f"{_PROJECT}", "",
        f"Open it in the [data browser]({DATA_BROWSER}): each year is one "
        "PMTiles archive with a zoom handover — A5 r7 cell aggregates at "
        "z0–8 switching to the full field polygons (all parquet "
        "attributes) from z9.", "",
        "## Layers", "",
        "- `cells` (z0–8): per-cell `count`, `area_ha`, `avg_score`, "
        "`pct_covered` (A5 r7, ≈2,075.5 km² per cell).",
        "- `fields` (z9–13): every predicted parcel with `id`, "
        "`metrics:area`, `metrics:perimeter`, `score` (constant columns "
        "are excluded).", "",
        "## Styles", "",
        *[f"- **{name}** — {spec['title']}"
          for name, spec in specs.items()], "",
        "The per-cell aggregates are also published as GeoParquet "
        "(`cells_a5r7_{year}.parquet`) for analysis; the source polygons "
        "live in the per-year [vector collections](../).", "",
    ])


def collection_agents(years: list[int]) -> str:
    year = max(years)
    return "\n".join([
        "# AGENTS.md — FTW fields-yearly (PMTiles)", "",
        "Guidance for AI agents. Every claim here is measured from the "
        "data or quoted from the pipeline.", "",
        f"- PMTiles per year at `{COLLECTION_URL}/fields-{{year}}.pmtiles` "
        "with layers `cells` (z0–8) and `fields` (z9–13).",
        "- For analysis prefer the GeoParquet: per-cell aggregates at "
        f"`{COLLECTION_URL}/cells_a5r7_{{year}}.parquet`, source polygons "
        f"in the per-year collections at `{PUBLIC_BASE}/vector/{{year}}/`.",
        "- `pct_covered` can slightly exceed 100: a parcel is assigned "
        "wholly to one cell, so boundary parcels contribute their full "
        "area there.", "",
        "Runnable example:", "",
        "```python",
        "import duckdb",
        "con = duckdb.connect()",
        'con.execute("INSTALL spatial; LOAD spatial; '
        'INSTALL httpfs; LOAD httpfs;")',
        f'url = "{COLLECTION_URL}/cells_a5r7_{year}.parquet"',
        "con.sql(f\"\"\"",
        "    SELECT count(*) AS cells, sum(count) AS parcels,",
        "           round(avg(pct_covered), 2) AS mean_pct",
        "    FROM read_parquet('{url}')",
        "\"\"\").show()",
        "```", "",
    ])


def collection_llms(years: list[int]) -> str:
    return "\n".join([
        "# FTW Global (beta) — fields-yearly PMTiles", "",
        "> One browsable PMTiles archive per year: A5 r7 cell aggregates "
        "z0–8 handing over to full field polygons z9–13. CC-BY-4.0.", "",
        *[f"- fields-{y}.pmtiles + cells_a5r7_{y}.parquet" for y in years],
        f"- Collection: {COLLECTION_URL}/collection.json", "",
    ])


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2025])
    parser.add_argument("--default-style", default="count")
    args = parser.parse_args()

    bins = load_bins()
    specs = style_specs(bins)
    years = sorted(args.years)

    for year in years:
        for name, spec in specs.items():
            write_json(OUT / "styles" / f"{name}-{year}.json",
                       build_style(year, name, spec))
    collection = build_collection(years, bins, args.default_style)
    for year in years:
        stamp_file_meta(collection["assets"][f"pmtiles_{year}"],
                        STAGING / f"fields-{year}.pmtiles")
        stamp_file_meta(collection["assets"][f"cells_{year}"],
                        STAGING / f"cells_a5r7_{year}.parquet")
    thumb = OUT / "thumbnail.png"
    if thumb.is_file():
        stamp_file_meta(collection["assets"]["thumbnail"], thumb)
    else:
        print("note: thumbnail.png absent (chiitiler render pending); "
              "asset left without file:*")
    write_json(OUT / "collection.json", collection)
    (OUT / "README.md").write_text(collection_readme(years, bins))
    (OUT / "AGENTS.md").write_text(collection_agents(years))
    (OUT / "llms.txt").write_text(collection_llms(years))
    print(f"OK -> {OUT} ({len(years)} year(s), {len(specs)} styles/year)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
