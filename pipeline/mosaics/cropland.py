"""Per-tile cropland fraction from Esri/Impact Observatory 10 m annual land cover."""

import re
from dataclasses import dataclass

import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.warp import transform_bounds

IOLULC_BASE = "https://io-10m-annual-lulc.s3.amazonaws.com"
IOLULC_CROPS_CLASS = 5
IOLULC_YEARS = (2017, 2020, 2024)
_HTTP_CODE = re.compile(r"HTTP response code:\s*(\d{3})")


def _is_absent(exc: Exception) -> bool:
    "Only 404/410 mean the tile does not exist; anything else is a transport error."
    m = _HTTP_CODE.search(str(exc))
    return bool(m) and int(m.group(1)) in (404, 410)


@dataclass
class CroplandStat:
    cropland_px: int
    valid_px: int

    @property
    def fraction(self) -> float:
        return self.cropland_px / self.valid_px if self.valid_px else 0.0


def _iolulc_url(tile_key: str, year: int) -> str:
    return f"/vsicurl/{IOLULC_BASE}/{tile_key[:3]}_{year}.tif"


def cropland_fraction(
    tile_key: str,
    bbox: tuple[float, float, float, float],
    *,
    years: tuple[int, ...] = IOLULC_YEARS,
    target_px: int = 512,
) -> CroplandStat:
    "Max crop fraction of ``bbox`` over ``years`` (max, since one list serves every year)."
    best = CroplandStat(0, 0)
    for year in years:
        url = _iolulc_url(tile_key, year)
        try:
            with rasterio.open(url) as ds:
                left, bottom, right, top = transform_bounds("EPSG:4326", ds.crs, *bbox)
                window = (
                    max(left, ds.bounds.left),
                    max(bottom, ds.bounds.bottom),
                    min(right, ds.bounds.right),
                    min(top, ds.bounds.top),
                )
                if window[0] >= window[2] or window[1] >= window[3]:
                    continue
                arr = ds.read(
                    1,
                    window=ds.window(*window),
                    out_shape=(target_px, target_px),
                    resampling=Resampling.nearest,
                )
        except RasterioIOError as exc:
            if _is_absent(exc):
                continue
            raise RasterioIOError(f"{url}: {exc}") from exc
        stat = CroplandStat(int((arr == IOLULC_CROPS_CLASS).sum()), int((arr != 0).sum()))
        if stat.fraction > best.fraction:
            best = stat
    return best
