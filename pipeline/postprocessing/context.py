"Read nodata, DEM, slope and land-cover QA rasters."

import math
import os
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from rasterio.windows import Window
from terrain import CROP, VSICURL_OPTS, WATER, _dem_name, _warp

#: B04 nodata in the CDSE sentinel-2-global-mosaics product (int16). Verified against
#: the band's own ``nodata`` on open rather than trusted.
NODATA = -32768

#: Columns of PR1's QA source index. ``b04_s3_href`` is the object its downloader
#: actually fetches; ``b04_odata_href`` is the OIDC-authenticated OData URL, which
#: GDAL cannot open, and is carried for provenance only. There is no public
#: unauthenticated https form of these assets, so there is no https fallback.
INDEX_COLUMNS = ["quarter", "b04_s3_href", "b04_s3_endpoint"]

#: IO annual land-cover vintages sampled per product year.
#:
#: ``year`` used to be accepted and ignored, so the 2020 product was scored against
#: 2024 land cover -- reading the future. The water mask takes the newest sampled
#: vintage and ``frac_crops_ever`` is "cropland in ANY sampled year", so the sample
#: must never reach past the product year. IO's series currently ends at 2024, which
#: is why 2025 shares 2024's vintages.
LULC_BY_YEAR = {
    2020: (2017, 2020),
    2024: (2017, 2020, 2024),
    2025: (2017, 2020, 2024),
}

IO_LULC = "https://io-10m-annual-lulc.s3.amazonaws.com"


def lulc_years(year: int) -> tuple[int, ...]:
    if year not in LULC_BY_YEAR:
        raise ValueError(
            f"no land-cover vintages defined for product year {year}; "
            f"known years are {sorted(LULC_BY_YEAR)}. Add a row to LULC_BY_YEAR "
            "rather than letting a mismatched vintage through."
        )
    return LULC_BY_YEAR[year]


def dem_lons(west: float, east: float) -> list[int]:
    """Integer DEM longitudes covering [west, east], handling the antimeridian.

    ``transform_bounds`` reports a dateline-crossing tile as west > east -- a
    measured zone-1 tile gives w = 178.913, e = -178.779 -- and NOT as an unwrapped
    span. The old guard tested ``e - w > 180``, which is -357.69 for that tile, so
    the plain branch ran and ``range(178, -178)`` enumerated NOTHING: no DEM tile was
    fetched at all, silently, and every parcel on an antimeridian tile got a NaN
    elevation (published as slope 0.0 before the slope fix).
    """
    if east < west:
        return [*range(math.floor(west), 180), *range(-180, math.ceil(east))]
    return list(range(math.floor(west), math.ceil(east)))


def _vsis3(s3_href: str, endpoint: str) -> tuple[str, dict]:
    "A /vsis3 path and the GDAL options for the endpoint the index row was written against."
    if not s3_href.startswith("s3://"):
        raise ValueError(f"expected an s3:// href, got {s3_href!r}")
    if not endpoint:
        raise ValueError(f"{s3_href}: index row carries no b04_s3_endpoint")
    bucket, _, key = s3_href.removeprefix("s3://").partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed s3 href {s3_href!r}")
    return f"/vsis3/{bucket}/{key}", {
        **VSICURL_OPTS,
        # The row's own endpoint, never a hardcoded one: PR1 honours
        # EODATA_S3_ENDPOINT and the index records what it used.
        "AWS_S3_ENDPOINT": endpoint,
        "AWS_VIRTUAL_HOSTING": "FALSE",
    }


#: 10 m source pixels per 40 m QA cell, per axis.
QA_FACTOR = 4


def nodata_cells(ds, h40: int, w40: int, stripe: int = 512) -> np.ndarray:
    """Which 40 m cells contain ANY nodata source pixel, reduced at FULL resolution.

    Never compare a resampled value to an exact sentinel. ``ds.read(1,
    out_shape=...)`` at 4x decimation is served from the COG's overviews, which are
    built by interpolation, so a cell straddling a swath edge comes back as some
    blend that never equals -32768. Measured on a ragged-swath fixture with cubic
    overviews, the value comparison missed 494 of 2,086 partially-nodata cells
    (24%) -- and ``read_masks`` at a reduced ``out_shape`` is defeated the same way,
    because GDAL derives the overview mask from the overview values. So the
    reduction happens here, over full-resolution pixels, in row stripes aligned to
    the 4x factor.

    A cell counts as nodata for its quarter if ANY of its 16 source pixels is,
    which is the conservative reading frac_nodata_1q/3q exist to give.
    """
    f = QA_FACTOR
    if (ds.height, ds.width) != (h40 * f, w40 * f):
        raise ValueError(
            f"source mosaic is {ds.height}x{ds.width}, expected {h40 * f}x{w40 * f} "
            "to reduce exactly onto the 40 m QA grid"
        )
    nodata = ds.nodata if ds.nodata is not None else NODATA
    out = np.zeros((h40, w40), bool)
    for r0 in range(0, h40, stripe):
        r1 = min(h40, r0 + stripe)
        band = ds.read(1, window=Window(0, r0 * f, w40 * f, (r1 - r0) * f))
        out[r0:r1] = (band == nodata).reshape(r1 - r0, f, w40, f).any(axis=(1, 3))
    return out


def _require_credentials() -> None:
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return
    if os.environ.get("AWS_NO_SIGN_REQUEST", "").upper() in ("YES", "TRUE", "1"):
        return
    raise RasterioIOError(
        "CDSE EODATA S3 needs credentials: set AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY (the EODATA keys, not an AWS account's). The QA "
        "source index's b04_odata_href is OIDC-authenticated and cannot be opened "
        "by GDAL, and these assets have no public https form."
    )


def aux_rasters(tk: str, year: int, index: Path, crs, tr10, shape10) -> dict:
    "Per-tile context rasters: nodata-quarter count (40 m), and on a 30 m grid:"
    vintages = lulc_years(year)
    t = pq.read_table(index, filters=[("tile_key", "=", tk)], columns=INDEX_COLUMNS).to_pylist()
    if len(t) != 4 or {r["quarter"] for r in t} != {"Q1", "Q2", "Q3", "Q4"}:
        raise ValueError(f"{tk}: expected exactly one source per quarter")
    _require_credentials()
    h40, w40 = shape10[0] // 4, shape10[1] // 4
    nod = np.zeros((h40, w40), np.uint8)
    for r in t:
        src, opts = _vsis3(r["b04_s3_href"], r["b04_s3_endpoint"])
        try:
            with rasterio.Env(**opts), rasterio.open(src) as ds:
                if ds.nodata is not None and ds.nodata != NODATA:
                    raise ValueError(
                        f"{tk} {r['quarter']}: {src} declares nodata {ds.nodata}, "
                        f"not the expected {NODATA}; the nodata-quarter count would be wrong"
                    )
                nod += nodata_cells(ds, h40, w40)
        except RasterioIOError as exc:
            # A bare GDAL message names neither the tile nor which of a tile's four
            # quarterly hrefs failed; mirror terrain._warp's guard and say.
            raise RasterioIOError(f"{tk} {r['quarter']}: cannot read {src}: {exc}") from exc
    h30, w30 = math.ceil(shape10[0] / 3), math.ceil(shape10[1] / 3)
    tr30 = tr10 * tr10.scale(3)
    w_, s_, e_, n_ = transform_bounds(crs, "EPSG:4326", *array_bounds(*shape10, tr10))
    lons = dem_lons(w_, e_)
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
    lulc = {
        y: _warp(
            [f"/vsicurl/{IO_LULC}/{tk[:3]}_{y}.tif"],
            (h30, w30),
            crs,
            tr30,
            Resampling.mode,
            np.uint8,
        )
        for y in vintages
    }
    crops = np.zeros((h30, w30), bool)
    for a in lulc.values():
        crops |= a == CROP
    return {
        "nod": nod,
        "dem": dem,
        "slope": slope,
        "water": lulc[max(vintages)] == WATER,
        "crops": crops,
    }
