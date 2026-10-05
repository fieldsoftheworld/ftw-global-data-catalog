#!/usr/bin/env python3
"""Render the raster collection thumbnails from the index footprints.

Portolan (PTL-VIZ-001) wants every geospatial collection to carry a
``thumbnail`` asset. For the raster tree the honest render available is the
data's own shape from the ``index/raster.parquet`` manifest, over the FTW
app's dark background, framed 3:2 for the browser card: the ~7,466 tile
footprints per year, shaded by ``field_frac`` (the fraction of field pixels
in the tile) — a real low-res field-density map of the world. Phase 4's
per-year COG mosaics will replace it with a render of the pixels themselves.

    .venv/bin/python3 tools/make_index_thumbnails.py raster

The vector tree is **not** rendered here. It has PMTiles and published styles,
so ``tools/render_thumbnails.py`` renders each year through chiitiler from the
style its collection titles — and running this script over ``vector`` would
overwrite those nine renders with a UTM-zone bar chart while the titles kept
promising the styled render. That is the exact defect PR #18 fixed, so the
mode is gone rather than merely discouraged; ``tests/test_thumbnails.py``
fails if the bytes stop being the style's render.

Writes ``catalog/raster/{year}/thumbnail.png``. The item builders register the
asset and stamp its ``file:size``/``file:checksum``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_BASE = "https://data.source.coop/ftw/global-data-2e"
BG = "#0b1414"  # the FTW inference app's dark background

# Dark teal -> FTW green ramp, readable on the dark card.
CMAP = LinearSegmentedColormap.from_list(
    "ftw_green", ["#12351f", "#1f7a33", "#33a02c", "#7fdc5a"]
)

TREES = {
    # tree -> (weight column, vmax percentile of the weight, edge width)
    # `vector` is deliberately absent: tools/render_thumbnails.py owns those
    # nine images. See the module docstring.
    "raster": ("field_frac", 98.0, 0.0),
}


def rings(geom: dict):
    """Exterior rings of a GeoJSON (Multi)Polygon."""
    if geom["type"] == "Polygon":
        yield geom["coordinates"][0]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield poly[0]


def render(rows: list[tuple], dest: Path, pct: float, edge: float) -> None:
    import numpy as np

    weights = [r[1] for r in rows]
    norm = Normalize(vmin=0, vmax=float(np.percentile(weights, pct)) or 1.0)
    verts, colors = [], []
    for geom_json, weight in rows:
        color = CMAP(norm(weight))
        for ring in rings(json.loads(geom_json)):
            verts.append(ring)
            colors.append(color)
    fig, ax = plt.subplots(figsize=(6, 4), dpi=200)  # 1200x800, 3:2
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.add_collection(PolyCollection(
        verts, facecolors=colors,
        edgecolors=BG if edge else "none", linewidths=edge,
    ))
    ax.set_xlim(-180, 180)
    ax.set_ylim(-60, 84)
    ax.set_aspect("auto")
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, facecolor=BG)
    plt.close(fig)
    print(f"{dest} ({dest.stat().st_size / 1024:.0f} KB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", choices=sorted(TREES))
    args = parser.parse_args()
    weight_col, pct, edge = TREES[args.tree]

    import duckdb

    con = duckdb.connect(
        config={"custom_user_agent": "Mozilla/5.0 (ftw-global-data-catalog)"}
    )
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.execute("SET http_retries=20;")
    years = con.execute(f"""
        SELECT year, list(ST_AsGeoJSON(geometry)), list({weight_col})
        FROM '{PUBLIC_BASE}/index/{args.tree}.parquet'
        GROUP BY year ORDER BY year
    """).fetchall()
    for year, geoms, weights in years:
        render(list(zip(geoms, weights)),
               ROOT / "catalog" / args.tree / str(year) / "thumbnail.png",
               pct, edge)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
