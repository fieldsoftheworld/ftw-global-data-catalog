#!/usr/bin/env python3
"""Render catalog thumbnails from COG overviews.

Ported from the alpha catalog's ``scripts/catalog/make_thumbnails.py``
(fieldsoftheworld/ftw-data-catalog@fd844c4). The reusable core is
``render_thumbnail``: read a decimated overview over the network (no full
download), mask nodata to transparency, apply a colormap, and composite over
the FTW app's dark background.

Phase 4 of docs/plan.md extends this with the per-item batch driver: one PNG
per 2e COG (67k renders, an sbatch array on rails, reading the ``field``
band from each COG's smallest overview) plus per-year collection mosaics.
Until then this module carries the rendering core only; PMTiles collection
thumbnails come from chiitiler (the portolan-thumbnails skill), not from
here.

Requires rasterio, numpy, matplotlib, pillow (the rails ftw venv has them).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import rasterio
import matplotlib

matplotlib.use("Agg")
from PIL import Image  # noqa: E402

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

BG = (11, 20, 20)  # #0b1414, the FTW inference app's dark background
WIDTH = 1280


def render_thumbnail(
    url: str,
    dest: Path,
    band: int = 1,
    cmap=None,
    vmin: float = 0.0,
    vmax: float | None = None,
    nodata: float | None = None,
    width: int = WIDTH,
) -> Path:
    """Render one COG band to a PNG thumbnail at ``dest``.

    Reads a decimated overview sized to ``width`` (rasterio picks the
    cheapest overview), so a remote render moves kilobytes, not the file.
    ``vmax=None`` stretches to the 98th percentile of the valid pixels.
    Nodata (given, or the file's) becomes transparency, composited over the
    dark app background so sparse coverage reads as data on a map, not as
    a broken image.
    """
    if cmap is None:
        cmap = matplotlib.colormaps["viridis"]
    with rasterio.open(url) as ds:
        height = max(1, round(width * ds.height / ds.width))
        arr = ds.read(band, out_shape=(height, width),
                      masked=True).astype("float64")
        nd = nodata if nodata is not None else ds.nodatavals[band - 1]
    data = np.ma.masked_invalid(arr)
    if nd is not None:
        data = np.ma.masked_equal(data, nd)
    valid = data.compressed()
    if vmax is None:
        vmax = float(np.percentile(valid, 98)) if valid.size else 1.0
        if vmax <= vmin:
            vmax = vmin + 1.0
    norm = np.clip((data.filled(vmin) - vmin) / (vmax - vmin), 0, 1)
    rgba = (cmap(norm) * 255).astype("uint8")  # HxWx4
    rgba[..., 3] = np.where(np.ma.getmaskarray(data), 0, 255)
    fg = Image.fromarray(rgba, "RGBA")
    bg = Image.new("RGBA", fg.size, BG + (255,))
    out = Image.alpha_composite(bg, fg).convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, optimize=True)
    return dest


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Render one COG band to a PNG thumbnail."
    )
    parser.add_argument("url", help="COG URL or path")
    parser.add_argument("dest", type=Path, help="output PNG path")
    parser.add_argument("--band", type=int, default=1)
    parser.add_argument("--cmap", default="viridis")
    parser.add_argument("--vmin", type=float, default=0.0)
    parser.add_argument("--vmax", type=float, default=None)
    parser.add_argument("--nodata", type=float, default=None)
    args = parser.parse_args()
    dest = render_thumbnail(
        args.url, args.dest, band=args.band,
        cmap=matplotlib.colormaps[args.cmap],
        vmin=args.vmin, vmax=args.vmax, nodata=args.nodata,
    )
    print(dest)


if __name__ == "__main__":
    main()
