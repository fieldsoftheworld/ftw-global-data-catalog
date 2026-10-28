"""Reproject public DEM and land-cover context rasters."""

import re
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.warp import reproject

DEM = "https://copernicus-dem-30m.s3.amazonaws.com"
CROP, WATER = 5, 1

#: GDAL options for every ``/vsicurl`` read in this package, applied per open rather than
#: through ``os.environ`` at import. ``EMPTY_DIR`` suppresses sidecar probing. There is no
#: ``CPL_VSIL_CURL_ALLOWED_EXTENSIONS``, because QA source hrefs are not all ``*.tif``
#: (CDSE serves ``.../Nodes(B04.tif)/$value``) and the allow-list makes GDAL refuse them
#: before any request. GDAL does not retry HTTP by default, so a transient 5xx/429 from
#: the DEM, land-cover or EODATA buckets would fail the tile; retry 6 times, 5 s apart
#: (GDAL backs off exponentially).
VSICURL_OPTS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MAX_RETRY": "6",
    "GDAL_HTTP_RETRY_DELAY": "5",
}


def _dem_name(lat: int, lon: int) -> str:
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    stem = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
    return f"/vsicurl/{DEM}/{stem}/{stem}.tif"


#: HTTP statuses that mean "this DEM/land-cover tile does not exist". Anything else
#: (401, 403, 429, any 5xx, a CURL timeout, a DNS failure) is a transport or credentials
#: problem and must not be recorded as missing coverage.
ABSENT_HTTP = frozenset({404, 410})
_HTTP_CODE = re.compile(r"HTTP response code:\s*(\d{3})")
_REMOTE = ("/vsicurl/", "/vsis3/", "/vsigs/", "/vsiaz/", "http://", "https://")


def is_absent(src: str, exc: Exception) -> bool:
    """True when ``exc`` means the tile does not exist.

    Classified on the HTTP status, or for a local path by asking the filesystem, never
    by matching error text. "No such file or directory" is locale-dependent, and GDAL
    uses it for several unrelated refusals (an extension allow-list rejection among
    them), so matching it would record a 403 or a timeout as a hole in the DEM.
    """
    m = _HTTP_CODE.search(str(exc))
    if m:
        return int(m.group(1)) in ABSENT_HTTP
    if not src.startswith(_REMOTE):
        return not Path(src).exists()
    return False


def _warp(srcs: list[str], shape, crs, transform, resampling, dtype) -> np.ndarray:
    """Reproject sources onto one grid; an absent tile is missing coverage (NaN/0).

    Any other read error raises.
    """
    out = np.full(shape, np.nan if dtype == np.float32 else 0, dtype)
    for src in srcs:
        try:
            with rasterio.Env(**VSICURL_OPTS), rasterio.open(src) as ds:
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
            if not is_absent(src, exc):
                raise RasterioIOError(f"{src}: {exc}") from exc
    return out
