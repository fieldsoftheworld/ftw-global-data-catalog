"""Cropland fraction per MGRS tile, from Esri/Impact Observatory 10 m annual land cover
over ``/vsicurl``. ``keeplist.py`` uses this to decide which tiles are worth downloading
at all.

A missing io-lulc tile -- the object genuinely does not exist, HTTP 404/410 -- counts as 0
cropland for that epoch. Any other failure (a timeout, a 403, a 5xx) is NOT absence and is
re-raised: pipeline/postprocessing/terrain.py draws the same line for the same bucket,
because swallowing a transient fetch error here would silently and permanently drop a real
cropland tile from the keep-list instead of just costing a retry.
"""

import logging
import re
from dataclasses import dataclass

import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)

# Esri / Impact Observatory 10 m annual land cover. Objects are "{zone}{band}_{year}.tif",
# e.g. "15T_2024.tif" -- the same bucket and vintages terrain.py/context.py sample for
# per-parcel QA land cover, not WorldCover, which only covers 2020/2021.
IOLULC_BASE = "https://io-10m-annual-lulc.s3.amazonaws.com"
IOLULC_CROPS_CLASS = 5
IOLULC_YEARS = (2017, 2020, 2024)

# Only 404/410 mean "this tile is not there"; everything else is a transport problem,
# the same classification terrain.is_absent uses for the DEM/land-cover buckets.
_ABSENT_HTTP = frozenset({404, 410})
_HTTP_CODE = re.compile(r"HTTP response code:\s*(\d{3})")


def _is_absent(exc: Exception) -> bool:
    m = _HTTP_CODE.search(str(exc))
    return bool(m) and int(m.group(1)) in _ABSENT_HTTP


@dataclass
class CroplandStat:
    """Cropland coverage for one tile bbox."""

    cropland_px: int
    valid_px: int

    @property
    def fraction(self) -> float:
        return self.cropland_px / self.valid_px if self.valid_px else 0.0


def _iolulc_url(tile_key: str, year: int) -> str:
    """io-lulc object for a mosaic tile key: `15SVD_0_0` + 2024 -> `15S_2024.tif` (first 3 chars)."""
    return f"/vsicurl/{IOLULC_BASE}/{tile_key[:3]}_{year}.tif"


def cropland_fraction_iolulc(
    tile_key: str,
    bbox: tuple[float, float, float, float],
    *,
    years: tuple[int, ...] = IOLULC_YEARS,
    target_px: int = 512,
) -> CroplandStat:
    """Max crop fraction of ``bbox`` across io-lulc ``years``.

    Max, not latest: the keep-list is reused for every imagery year, so a tile that was
    cropland in any epoch must be kept; drift can only cost a wasted tile, never a false
    skip.
    """
    best = CroplandStat(cropland_px=0, valid_px=0)
    for year in years:
        url = _iolulc_url(tile_key, year)
        try:
            with rasterio.open(url) as ds:
                left, bottom, right, top = transform_bounds("EPSG:4326", ds.crs, *bbox)
                inter = (
                    max(left, ds.bounds.left),
                    max(bottom, ds.bounds.bottom),
                    min(right, ds.bounds.right),
                    min(top, ds.bounds.top),
                )
                if inter[0] >= inter[2] or inter[1] >= inter[3]:
                    continue
                arr = ds.read(
                    1,
                    window=ds.window(*inter),
                    out_shape=(target_px, target_px),
                    resampling=Resampling.nearest,
                )
        except RasterioIOError as exc:
            if not _is_absent(exc):
                raise RasterioIOError(f"{url}: {exc}") from exc
            logger.debug("no io-lulc tile %s_%d (treating as 0 cropland)", tile_key[:3], year)
            continue
        stat = CroplandStat(
            cropland_px=int((arr == IOLULC_CROPS_CLASS).sum()),
            valid_px=int((arr != 0).sum()),
        )
        if stat.fraction > best.fraction:
            best = stat
    return best
