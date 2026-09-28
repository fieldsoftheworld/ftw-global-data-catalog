#!/usr/bin/env python3
"""Render the vector year-collection thumbnails from the index footprints.

Portolan (PTL-VIZ-001) wants every geospatial collection to carry a
``thumbnail`` asset. The year collections have no PMTiles yet (that is the
Phase 3 product, whose thumbnails come from chiitiler), so the honest render
available today is the data's own shape: the 54 UTM-zone footprints from
``index/vector.parquet``, shaded by parcel count, over the FTW app's dark
background, framed 3:2 for the browser card.

    .venv/bin/python3 tools/make_vector_thumbnails.py

Writes ``catalog/vector/{year}/thumbnail.png``. Re-run after the index
changes; ``tools/build_vector_items.py`` registers the asset and stamps its
``file:size``/``file:checksum``.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "vector"
PUBLIC_BASE = "https://data.source.coop/ftw/global-data-beta"
BG = "#0b1414"  # the FTW inference app's dark background

# Dark teal -> FTW green ramp, readable on the dark card.
CMAP = LinearSegmentedColormap.from_list(
    "ftw_green", ["#12351f", "#1f7a33", "#33a02c", "#7fdc5a"]
)


def rings(geom: dict):
    """Exterior rings of a GeoJSON (Multi)Polygon."""
    if geom["type"] == "Polygon":
        yield geom["coordinates"][0]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield poly[0]


def render(year: int, rows: list[tuple], dest: Path) -> None:
    counts = [r[1] for r in rows]
    norm = Normalize(vmin=0, vmax=max(counts))
    fig, ax = plt.subplots(figsize=(6, 4), dpi=200)  # 1200x800, 3:2
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for geom_json, count in rows:
        color = CMAP(norm(count))
        for ring in rings(json.loads(geom_json)):
            ax.add_patch(MplPolygon(
                ring, closed=True, facecolor=color,
                edgecolor=BG, linewidth=0.3,
            ))
    ax.set_xlim(-180, 180)
    ax.set_ylim(-60, 84)
    ax.set_aspect("auto")
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(dest, facecolor=BG)
    plt.close(fig)
    print(f"{dest} ({dest.stat().st_size / 1024:.0f} KB)")


def main() -> int:
    import duckdb

    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-global-data-catalog)"}
    )
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET http_retries=20;")
    years = con.execute(f"""
        SELECT year, list(ST_AsGeoJSON(geometry)), list(n_parcels)
        FROM '{PUBLIC_BASE}/index/vector.parquet'
        GROUP BY year ORDER BY year
    """).fetchall()
    for year, geoms, counts in years:
        render(year, list(zip(geoms, counts)),
               OUT / str(year) / "thumbnail.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
