"""Per-tile cropland area from Esri/Impact Observatory 10 m annual land cover."""

import re
from dataclasses import dataclass

import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.warp import transform_bounds

IOLULC_BASE = "https://io-10m-annual-lulc.s3.amazonaws.com"
IOLULC_CROPS_CLASS = 5
IOLULC_YEARS = (2017, 2020, 2024)
MGRS_BANDS = "CDEFGHJKLMNPQRSTUVWX"
_HTTP_CODE = re.compile(r"HTTP response code:\s*(\d{3})")


def _is_absent(exc: Exception) -> bool:
    "Only HTTP 404 or 410 means the tile does not exist."
    m = _HTTP_CODE.search(str(exc))
    return bool(m) and int(m.group(1)) in (404, 410)


@dataclass
class CroplandStat:
    crop_km2: float = 0.0
    land_km2: float = 0.0

    @property
    def fraction(self) -> float:
        return self.crop_km2 / self.land_km2 if self.land_km2 else 0.0


def _iolulc_url(gzd: str, year: int) -> str:
    return f"/vsicurl/{IOLULC_BASE}/{gzd}_{year}.tif"


def _bands(south: float, north: float) -> list[tuple[str, float, float]]:
    out = []
    for i, letter in enumerate(MGRS_BANDS):
        lo, hi = -80 + 8 * i, (84 if letter == "X" else -72 + 8 * i)
        if south < hi and north > lo:
            out.append((letter, lo, hi))
    return out


def _read_part(
    url: str, bbox: tuple[float, float, float, float], target_px: int
) -> CroplandStat | None:
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
                return None
            win = ds.window(*window)
            arr = ds.read(
                1, window=win, out_shape=(target_px, target_px), resampling=Resampling.nearest
            )
            cell_km2 = win.width * win.height * abs(ds.res[0] * ds.res[1]) / 1e6 / arr.size
    except RasterioIOError as exc:
        if _is_absent(exc):
            return None
        raise RasterioIOError(f"{url}: {exc}") from exc
    return CroplandStat(
        int((arr == IOLULC_CROPS_CLASS).sum()) * cell_km2, int((arr != 0).sum()) * cell_km2
    )


def cropland_stats(
    tile_key: str,
    bbox: tuple[float, float, float, float],
    *,
    years: tuple[int, ...] = IOLULC_YEARS,
    target_px: int = 512,
) -> dict[int, CroplandStat]:
    "Crop and land area of ``bbox`` per io-lulc year, read from every MGRS band it crosses."
    west, south, east, north = bbox
    stats = {}
    for year in years:
        parts = [
            _read_part(
                _iolulc_url(f"{tile_key[:2]}{band}", year),
                (west, max(south, lo), east, min(north, hi)),
                target_px,
            )
            for band, lo, hi in _bands(south, north)
        ]
        parts = [p for p in parts if p]
        if parts:
            stats[year] = CroplandStat(
                sum(p.crop_km2 for p in parts), sum(p.land_km2 for p in parts)
            )
    return stats
