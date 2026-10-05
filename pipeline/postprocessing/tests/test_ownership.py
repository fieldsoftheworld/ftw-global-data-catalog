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


#: Real published score rasters (2025): tile, west edge, north edge, expected 100 km square.
#: Every one is 40,032 px x 2.5 m = 100,080 m wide, with the origin 0-80 m off its square.
REAL_TILES = [
    ("15TVG_0_0", 399_960.0, 4_700_040.0, (400_000.0, 4_600_000.0, 500_000.0, 4_700_000.0)),
    ("01KFS_0_0", 600_000.0, 7_700_020.0, (600_000.0, 7_600_000.0, 700_000.0, 7_700_000.0)),
    # southern hemisphere, the two tiles the origin rounding was suspected of mis-owning.
    # 59GQQ_0_1 is a stacked sub-tile: its square is the 59GQP cell, NOT the one its key
    # names -- see test_a_stacked_sub_tile_owns_the_cell_its_key_does_not_name.
    ("59GQQ_0_0", 699_960.0, 5_500_000.0, (700_000.0, 5_400_000.0, 800_000.0, 5_500_000.0)),
    ("59GQQ_0_1", 699_960.0, 5_399_920.0, (700_000.0, 5_300_000.0, 800_000.0, 5_400_000.0)),
    ("60GTU_0_0", 199_980.0, 5_400_040.0, (200_000.0, 5_300_000.0, 300_000.0, 5_400_000.0)),
    # Norway, band 32V (the tiles the 31V/32V exception exists for)
    ("32VKL_0_0", 199_980.0, 6_600_000.0, (200_000.0, 6_500_000.0, 300_000.0, 6_600_000.0)),
    ("32VLL_0_0", 300_000.0, 6_600_000.0, (300_000.0, 6_500_000.0, 400_000.0, 6_600_000.0)),
    # the equator, in both hemispheres' false-northing conventions
    ("17MNV_0_0", 499_980.0, 10_000_000.0, (500_000.0, 9_900_000.0, 600_000.0, 10_000_000.0)),
    ("17NPA_0_0", 600_000.0, 100_020.0, (600_000.0, 0.0, 700_000.0, 100_000.0)),
]
SIDE = 40_032


def _real(c: float, f: float):
    return ol.mgrs_square(A(2.5, 0, c, 0, -2.5, f), SIDE, SIDE)


def _old_square(c: float, f: float) -> tuple[float, float, float, float]:
    "The previous formula: origin + 5 km, extent - 5 km (assumes a 110 km raster)."
    return c + 5_000, f - SIDE * 2.5 + 5_000, c + SIDE * 2.5 - 5_000, f - 5_000


@pytest.mark.parametrize("tile,c,f,expected", REAL_TILES, ids=[t[0] for t in REAL_TILES])
def test_mgrs_square_is_the_100km_cell_the_raster_covers(tile, c, f, expected):
    "The cell the pixels snap to -- for a `_r_c` sub-tile not the cell its key names."
    assert _real(c, f) == expected, tile


#: MGRS 100 km row letters, A-V without I and O, repeating every 2,000 km of northing.
ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"


def _row_letter(y: float) -> str:
    "The MGRS row letter of the 100 km band starting at northing `y`."
    return ROW_LETTERS[int(y // 100_000) % 20]


def test_a_stacked_sub_tile_owns_the_cell_its_key_does_not_name():
    """59GQQ_0_1 owns the 59GQ**P** cell, and that is correct, not a bug to fix.

    A `_0_1` sub-tile starts 100,080 m below `_0_0`, so its origin rounds to the
    *neighbouring* row: the square follows the raster's pixels, which is what
    in_mgrs_square must test, while the tile key still carries `_0_0`'s letters.
    27 published items are of this shape (scanned over all 67,197 raster items);
    reconciling the square with the key would hand each of them a 100 km cell its
    raster does not cover.
    """
    x0, y0, x1, y1 = _real(699_960.0, 5_399_920.0)
    assert (y0, y1) == (5_300_000.0, 5_400_000.0)
    assert _row_letter(y0) == "P", "the raster covers the P row; the key says Q"
    # the sibling above it is the tile whose key the letters do match
    assert _row_letter(_real(699_960.0, 5_500_000.0)[1]) == "Q"


def test_stacked_sub_tiles_abut_but_leave_an_80m_unowned_seam():
    """Measured loss: the 80 m strip between two stacked sub-tiles belongs to nobody.

    The squares abut exactly at y = 5,400,000, but the rasters abut 80 m lower, at
    5,399,920: that band has pixels only in `_0_0` while belonging to `_0_1`'s
    square, so its parcels get in_mgrs_square=False in `_0_0` and have no pixels at
    all in `_0_1`, and merge_polygons drops them. ~8 km2 per affected item; only
    59GQQ has both siblings published, so 9 items, one per year. Everywhere else the
    80 m raster overhang covers the seam -- this is the one gap the snapping leaves.
    """
    upper = _real(699_960.0, 5_500_000.0)  # 59GQQ_0_0
    lower = _real(699_960.0, 5_399_920.0)  # 59GQQ_0_1
    assert upper[1] == lower[3] == 5_400_000.0, "distinct, exactly abutting squares"
    seam = 5_500_000.0 - SIDE * 2.5  # _0_0's south data edge == _0_1's north edge
    assert seam == 5_399_920.0
    mid = (seam + 5_400_000.0) / 2  # a centroid in the 80 m strip
    assert lower[1] < mid <= lower[3], "the strip is in the lower tile's square ..."
    assert mid > seam, "... but above the lower raster's north edge, so it has no pixels there"
    assert not upper[1] < mid <= upper[3], "and the upper tile, whose pixels cover it, disclaims it"


@pytest.mark.parametrize("tile,c,f,expected", REAL_TILES, ids=[t[0] for t in REAL_TILES])
def test_no_tile_loses_area_to_a_shrunken_square(tile, c, f, expected):
    """The old formula claimed 90.08 x 90.08 km (81%): a 5 km frame owned by nobody."""
    x0, y0, x1, y1 = _real(c, f)
    assert (x1 - x0) * (y1 - y0) == pytest.approx(ol.MGRS_SQUARE_M**2)
    ox0, oy0, ox1, oy1 = _old_square(c, f)
    assert (ox1 - ox0) * (oy1 - oy0) < 0.82 * ol.MGRS_SQUARE_M**2  # the bug this guards
    # a parcel 2 km inside the true edge was dropped by the old square, and is owned now
    px, py = x0 + 2_000, y1 - 2_000
    assert x0 <= px < x1 and y0 < py <= y1
    assert not (ox0 <= px < ox1 and oy0 < py <= oy1)


def test_neighbouring_tiles_own_exactly_abutting_squares():
    "The frame between two neighbours belongs to one of them, never to neither."
    west = _real(300_000.0, 6_600_000.0)   # 32VLL
    east = _real(399_960.0, 6_600_000.0)   # a tile one square east, origin 40 m off
    south = _real(300_000.0, 6_500_040.0)  # 32VLK-like, one square south
    assert west[2] == east[0]
    assert west[1] == south[3]


def test_a_parcel_straddling_a_same_zone_seam_is_claimed_by_both_neighbours():
    """Abutting squares make the cross-tile seam union load-bearing, not optional.

    Under the old 5 km inset no owned parcel could reach the raster edge. Now the
    two rasters share only a 40-120 m overlap band (here 399,960-400,080), so a
    field spanning the seam is cut at each raster's own data edge -- which
    `touches_window_edge` does not flag, by construction (outlines only marks the
    *window* borders interior to the raster) -- and each clipped half's pixel mass
    centroid falls inside its own tile's square. Both halves are therefore owned
    and published, and only fiboa_convert's seam union collapses them into one
    field; above ~1.2 km each side of the seam even that fails (see
    test_a_wide_field_across_a_same_zone_seam_is_not_rejoined).
    """
    west = _real(300_000.0, 6_600_000.0)   # 32VLL, data 300,000-400,080
    east = _real(399_960.0, 6_600_000.0)   # its eastern neighbour, data 399,960-500,040
    assert west[2] == east[0] == 400_000.0
    inside = lambda sq, x: sq[0] <= x < sq[2]  # noqa: E731
    # a 4 km field centred on the seam: each tile sees a ~2 km half clipped at its edge
    w_half, e_half = (398_000.0 + 400_040.0) / 2, (399_960.0 + 402_000.0) / 2
    assert inside(west, w_half) and not inside(east, w_half)
    assert inside(east, e_half) and not inside(west, e_half)
    assert w_half != e_half, "two different parcels, one field: the union must rejoin them"


@pytest.mark.parametrize("dx,dy", [(-80.0, 0.0), (-40.0, 0.0), (0.0, 80.0), (0.0, -80.0), (40.0, 40.0)])
def test_mgrs_square_tracks_a_sub_pixel_origin_offset_without_moving_100km(dx, dy):
    """A tiny origin offset must not change which 100 km cell is claimed."""
    x0, y0, x1, y1 = _real(400_000.0 + dx, 4_700_000.0 + dy)
    assert (x0, y1) == (400_000.0, 4_700_000.0)


@pytest.mark.parametrize("pad", [0.0, 5_000.0, 10_000.0])
def test_mgrs_square_is_independent_of_halo_padding(pad):
    "A raster padded by `pad` on every side (110 km for 5 km) owns the same square."
    side = int((100_000 + 2 * pad) / 2.5)
    tr = A(2.5, 0, 400_000 - pad, 0, -2.5, 4_700_000 + pad)
    assert ol.mgrs_square(tr, side, side) == (400_000.0, 4_600_000.0, 500_000.0, 4_700_000.0)


def test_a_raster_that_cannot_contain_its_square_is_rejected():
    tr = A(2.5, 0, 400_000, 0, -2.5, 4_700_000)
    with pytest.raises(ValueError, match="does not contain"):
        ol.mgrs_square(tr, 30_000, 30_000)  # 75 km: too small


def test_mgrs_square_is_half_open_so_no_parcel_has_two_owners():
    x0, y0, x1, y1 = _real(399_960.0, 4_700_040.0)
    north = _real(399_960.0, 4_800_040.0)
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
