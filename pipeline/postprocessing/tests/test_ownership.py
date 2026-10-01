"""Parcel ownership: which tile may claim a parcel, and over which longitudes."""

import sys
from pathlib import Path

import pytest
import rasterio
from pyproj import CRS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outlines as ol

A = rasterio.Affine

#: lon, lat of three Norwegian field areas, all inside Sentinel-2 band 32V.
NORWAY = {"Bergen": 5.32, "Stavanger": 5.73, "Jaeren": 5.60}


@pytest.mark.parametrize("place,lon", NORWAY.items())
def test_zone_32v_is_widened_to_3e(place, lon):
    """32V spans 3-12E on the Sentinel-2 grid, not the nominal 6-12E.

    The nominal band rejected every parcel between 3E and 6E, and no 31V tile
    covers 3-6E, so those parcels were dropped with exit 0.
    """
    assert ol.lon_in_zone(32, "V", lon), place
    assert not ol.lon_in_zone(31, "V", lon), f"{place} must not be claimed twice"


def test_zone_31v_is_narrowed_to_0_3e():
    assert ol.lon_in_zone(31, "V", 1.5)
    assert not ol.lon_in_zone(31, "V", 4.0)
    assert ol.lon_in_zone(32, "V", 4.0)


def test_nominal_bands_elsewhere_are_unchanged():
    assert ol.lon_in_zone(31, "U", 1.5)
    assert not ol.lon_in_zone(31, "U", 7.0)
    assert ol.lon_in_zone(32, "U", 7.0)
    assert ol.lon_in_zone(1, "C", -179.0)
    assert ol.lon_in_zone(60, "C", 179.0)


def test_band_x_is_rejected_not_guessed():
    with pytest.raises(ValueError, match="band X"):
        ol.lon_in_zone(32, "X", 9.0)


def _square(dx: float = 0.0, dy: float = 0.0):
    "A 110 km / 2.5 m tile whose 100 km square is 400000-500000 E, 5300000-5400000 N."
    h = w = int(110_000 / 2.5)
    tr = A(2.5, 0, 400_000 - 5_000 + dx, 0, -2.5, 5_400_000 + 5_000 + dy)
    return ol.mgrs_square(tr, h, w)


def test_mgrs_square_matches_the_nominal_square_when_the_origin_is_exact():
    assert _square() == (400_000.0, 5_300_000.0, 500_000.0, 5_400_000.0)


@pytest.mark.parametrize("dx,dy", [(-80.0, 0.0), (-40.0, 0.0), (0.0, 80.0), (0.0, -80.0)])
def test_mgrs_square_tracks_a_sub_pixel_origin_offset(dx, dy):
    """A tiny origin offset must move the square by that offset, not by 100 km.

    Two live CDSE tiles are offset (59GQQ_0_1 dy = -80 m, 60GTU_0_1 dy = -40 m).
    The nominal ``floor((tr.c + 5000) / 1e5)`` rounding measured sq_x0 = 300000
    for dx = -80, so the tile claimed its western neighbour's square and owned
    none of its own parcels.
    """
    x0, y0, x1, y1 = _square(dx, dy)
    assert (x0, y1) == (400_000.0 + dx, 5_400_000.0 + dy)
    assert x1 - x0 == pytest.approx(ol.MGRS_SQUARE_M)
    assert y1 - y0 == pytest.approx(ol.MGRS_SQUARE_M)


def test_mgrs_square_is_half_open_so_no_parcel_has_two_owners():
    x0, y0, x1, y1 = _square()
    north = _square(dy=100_000.0)
    inside = lambda sq, x, y: sq[0] <= x < sq[2] and sq[1] < y <= sq[3]  # noqa: E731
    assert inside((x0, y0, x1, y1), x0, y1)
    assert not inside((x0, y0, x1, y1), x1, y1)
    # the shared y boundary belongs to exactly one of the two tiles
    assert inside((x0, y0, x1, y1), 450_000.0, y1)
    assert not inside(north, 450_000.0, y1)
    assert inside(north, 450_000.0, y1 + 1.0)


def _mean_of(idx, cnt, n_id):
    "The same bincount mean outlines._run_tile builds per window."
    import numpy as np

    def mean_of(vals):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.bincount(idx, vals, n_id) / cnt

    return mean_of


def test_absent_dem_gives_nan_slope_not_flat_slope():
    """A parcel with no DEM coverage must report unknown slope, not 0 degrees.

    np.nan_to_num reported slope_mean 0.0 and frac_slope_gt30 0.0 while elev_mean
    stayed NaN, so coastal parcels with no DEM passed every slope QA filter as
    flat.
    """
    import numpy as np

    # parcel 1: all DEM missing; parcel 2: all steep; parcel 3: one missing sample
    idx = np.array([1, 1, 2, 2, 3, 3])
    cnt = np.array([0.0, 2.0, 2.0, 2.0])
    slope_px = np.array([np.nan, np.nan, 45.0, 45.0, 10.0, np.nan], dtype=np.float32)
    mean, steep = ol.slope_attrs(slope_px, _mean_of(idx, cnt, 4))
    assert np.isnan(mean[1]) and np.isnan(steep[1]), "no coverage -> unknown, not flat"
    assert mean[2] == pytest.approx(45.0) and steep[2] == pytest.approx(1.0)
    assert np.isnan(mean[3]) and np.isnan(steep[3]), "partial coverage -> unknown too"


def test_non_utm_crs_is_rejected_before_any_network_work():
    "utm_zone is None for 4326/3857/54009/5041; the old code raised TypeError late."
    for epsg in ("EPSG:4326", "EPSG:3857", "EPSG:5041"):
        assert CRS(epsg).utm_zone is None
    assert CRS("EPSG:32632").utm_zone == "32N"
