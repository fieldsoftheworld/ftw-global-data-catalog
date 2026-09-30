"Read nodata, DEM, slope and land-cover QA rasters."

import math
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from terrain import CROP, WATER, _dem_name, _warp

NODATA = -32768


def aux_rasters(tk: str, year: int, index: Path, crs, tr10, shape10) -> dict:
    "Per-tile context rasters: nodata-quarter count (40 m), and on a 30 m grid:"
    t = pq.read_table(
        index, filters=[("tile_key", "=", tk)], columns=["quarter", "b04_href"]
    ).to_pylist()
    if len(t) != 4 or {r["quarter"] for r in t} != {"Q1", "Q2", "Q3", "Q4"}:
        raise ValueError(f"{tk}: expected exactly one source per quarter")
    h40, w40 = shape10[0] // 4, shape10[1] // 4
    nod = np.zeros((h40, w40), np.uint8)
    for r in t:
        href = r["b04_href"]
        with rasterio.open("/vsicurl/" + href if href.startswith("http") else href) as ds:
            nod += ds.read(1, out_shape=(h40, w40), resampling=Resampling.nearest) == NODATA
    h30, w30 = math.ceil(shape10[0] / 3), math.ceil(shape10[1] / 3)
    tr30 = tr10 * tr10.scale(3)
    w_, s_, e_, n_ = transform_bounds(crs, "EPSG:4326", *array_bounds(*shape10, tr10))
    if e_ - w_ > 180:
        lons = [*range(math.floor(w_), 180), *range(-180, math.ceil(e_ - 360))]
    else:
        lons = range(math.floor(w_), math.ceil(e_))
    dem = _warp(
        [_dem_name(la, lo) for la in range(math.floor(s_), math.ceil(n_)) for lo in lons],
        (h30, w30),
        crs,
        tr30,
        Resampling.bilinear,
        np.float32,
    )
    gy, gx = np.gradient(dem, 30.0)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
    iol = "https://io-10m-annual-lulc.s3.amazonaws.com"
    lulc = {
        y: _warp(
            [f"/vsicurl/{iol}/{tk[:3]}_{y}.tif"], (h30, w30), crs, tr30, Resampling.mode, np.uint8
        )
        for y in (2017, 2020, 2024)
    }
    crops = np.zeros((h30, w30), bool)
    for a in lulc.values():
        crops |= a == CROP
    return {"nod": nod, "dem": dem, "slope": slope, "water": lulc[2024] == WATER, "crops": crops}
