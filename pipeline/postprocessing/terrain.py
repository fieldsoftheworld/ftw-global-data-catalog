"""Reproject public DEM and land-cover context rasters."""

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.warp import reproject

DEM = "https://copernicus-dem-30m.s3.amazonaws.com"
CROP, WATER = 5, 1


def _dem_name(lat: int, lon: int) -> str:
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    stem = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
    return f"/vsicurl/{DEM}/{stem}/{stem}.tif"


_MISSING = ("HTTP response code: 404", "No such file or directory")


def _warp(srcs: list[str], shape, crs, transform, resampling, dtype) -> np.ndarray:
    """Reproject sources onto one grid; a 404 or absent file is missing coverage (NaN/0), any other error raises."""
    out = np.full(shape, np.nan if dtype == np.float32 else 0, dtype)
    for src in srcs:
        try:
            with rasterio.open(src) as ds:
                reproject(
                    rasterio.band(ds, 1),
                    out,
                    dst_transform=transform,
                    dst_crs=crs,
                    resampling=resampling,
                    init_dest_nodata=False,
                    dst_nodata=np.nan if dtype == np.float32 else 0,
                )
        except RasterioIOError as exc:
            if not any(m in str(exc) for m in _MISSING):
                raise
    return out
