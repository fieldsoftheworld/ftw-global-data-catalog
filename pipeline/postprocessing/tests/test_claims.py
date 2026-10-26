"""Orphan-strip claims (ownership.py): parcels of a tile's square in a neighbour zone's
square that has no tile in the run."""

import csv
import sys
from pathlib import Path

import pytest
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ownership as own

# The Netherlands strip: zone 32 east of 6E at 52.7N has no mosaic tile for its 32UK* square
# (easting 200-300 km). 31UGU (zone 31, easting 700-800 km) images it; 32ULD starts at 300 km.
LAT = 52.7
SQUARES = frozenset({(31, True, 700000, 5900000), (32, True, 300000, 5900000)})
TO32 = Transformer.from_crs(32632, 4326, always_xy=True)
TO31 = Transformer.from_crs(32631, 4326, always_xy=True)
FROM31 = Transformer.from_crs(4326, 32631, always_xy=True)
_, N32 = Transformer.from_crs(4326, 32632, always_xy=True).transform(6.0, LAT)


def lon32(easting: float) -> float:
    return TO32.transform(easting, N32)[0]


def test_the_strip_belongs_to_the_western_zone():
    strip = lon32(299_000)
    assert 6.0 < strip < lon32(300_000)
    assert own.zone_of(strip, LAT) == 32
    assert not own.owned(32, strip, LAT, SQUARES)
    assert own.claimer(strip, LAT, SQUARES) == 31
    assert own.claimer(lon32(301_000), LAT, SQUARES) is None  # 32ULD's own square
    assert own.claimer(5.9, LAT, SQUARES) is None  # zone 31's own territory


def test_east_claimer_only_without_a_western_one():
    strip = lon32(299_000)
    assert own.claimer(strip, LAT, frozenset({(33, True, 200000, 5900000)})) is None
    e33, n33 = Transformer.from_crs(4326, 32633, always_xy=True).transform(strip, LAT)
    sq = frozenset({(33, True, int(e33 // 1e5 * 1e5), int(-(-n33 // 1e5) * 1e5))})
    assert own.claimer(strip, LAT, sq) == 33
    e31, n31 = FROM31.transform(strip, LAT)
    both = sq | {(31, True, int(e31 // 1e5 * 1e5), int(-(-n31 // 1e5) * 1e5))}
    assert own.claimer(strip, LAT, both) == 31


def _keeps31(x_west, x_east, y):
    lon, lat = TO31.transform((x_west + x_east) / 2, y)
    return own.zone_keeps(TO31, 31, "U", (lon, lat, y), x_west, x_east, True, SQUARES)


def test_strip_parcels_are_claimed():
    def x31(e32):
        return FROM31.transform(lon32(e32), LAT)[0]

    y31 = FROM31.transform(lon32(299_000), LAT)[1]
    assert _keeps31(x31(298_000), x31(299_500), y31) == (True, True)  # inside the strip
    assert _keeps31(x31(299_500), x31(301_000), y31) == (True, True)  # across 32ULD's edge
    assert _keeps31(x31(300_500), x31(301_500), y31) == (False, False)  # 32ULD's own square
    x = FROM31.transform(5.9, LAT)[0]
    assert _keeps31(x - 500, x + 500, y31) == (True, False)  # zone 31's own parcel
    lon, lat = TO31.transform(x31(299_000), y31)
    assert own.zone_keeps(TO31, 31, "U", (lon, lat, y31), 0, 1, False, SQUARES) == (False, False)


@pytest.mark.parametrize(
    ("lon", "lat", "zone"), [(2.99, 60.0, 31), (3.01, 60.0, 32), (3.01, 50.0, 31)]
)
def test_zone_of_band_v_edge_at_3e(lon, lat, zone):
    assert own.zone_of(lon, lat) == zone


def test_square_of_across_the_equator():
    assert own.square_of(50, 117.0, 0.001) == (50, True, 500000, 100000)
    assert own.square_of(50, 117.0, -0.001)[1:] == (False, 500000, 10_000_000)


def _squares(path):
    with path.open() as f:
        return {tuple(r.values()) for r in csv.DictReader(f)}


def test_the_current_squares_contain_the_2nd_edition_ones():
    here = Path(own.__file__).parent
    old, new = _squares(here / "owned_squares_2e.csv"), _squares(here / "owned_squares.csv")
    assert (len(old), len(new)) == (7467, 14608)
    assert old <= new


def test_build_squares_from_footprints(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    to_ll = Transformer.from_crs(32631, 4326, always_xy=True)
    # a 31UGU-like footprint: the 100 km square plus 40 m
    x = [699_960, 800_000, 800_000, 699_960, 699_960]
    y = [5_800_000, 5_800_000, 5_900_040, 5_900_040, 5_800_000]
    poly = shapely.Polygon(list(zip(*to_ll.transform(x, y), strict=True)))
    pq.write_table(pa.table({"tile_key": ["31UGU_0_0", "31UGT_0_0"],
                             "geometry": [shapely.to_wkb(poly)] * 2,
                             "antimeridian": [False, False]}), tmp_path / "fp.parquet")
    n = own.build_squares({"31UGU_0_0"}, tmp_path / "fp.parquet", tmp_path / "sq.csv")
    assert n == 1
    assert own.load_squares.__wrapped__(tmp_path / "sq.csv") == {(31, True, 700000, 5900000)}
