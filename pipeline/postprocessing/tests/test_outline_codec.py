import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import shapely
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outline_codec import CODEC_COLUMNS, RES, decode, encode, utm_epsg

EPSG = 32756


def _grid_geoms() -> np.ndarray:
    to_ll = Transformer.from_crs(EPSG, 4326, always_xy=True)

    def poly(*rings: list) -> shapely.Polygon:
        out = []
        for ring in rings:
            xs, ys = to_ll.transform(
                [500_000 + RES * i for i, _ in ring], [6_000_000 + RES * j for _, j in ring]
            )
            out.append(list(zip(xs, ys, strict=True)))
        return shapely.Polygon(out[0], out[1:])

    square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    hole = [(2, 2), (2, 4), (4, 4), (4, 2), (2, 2)]
    far = [(300, 300), (310, 300), (310, 310), (300, 300)]
    return np.array(
        [
            poly(square),
            poly(square, hole),
            shapely.MultiPolygon([poly(square), poly(far)]),
            shapely.MultiPolygon([poly(far, [(301, 301), (301, 302), (302, 302), (301, 301)])]),
            poly(far),
        ],
        dtype=object,
    )


def test_roundtrip_is_byte_identical():
    geoms = _grid_geoms()
    cols = encode(geoms, EPSG)
    assert set(cols) == set(CODEC_COLUMNS)
    back = decode({k: pa.chunked_array([v]) for k, v in cols.items()}, EPSG)
    assert np.array_equal(shapely.to_wkb(back), shapely.to_wkb(geoms))
    assert list(shapely.get_type_id(back)) == [3, 3, 6, 6, 3]


def test_all_single_polygons():
    geoms = _grid_geoms()[[0, 1, 4]]
    back = decode(encode(geoms, EPSG), EPSG)
    assert np.array_equal(shapely.to_wkb(back), shapely.to_wkb(geoms))


def test_utm_epsg():
    assert utm_epsg("56JLQ_0_0") == 32756
    assert utm_epsg("04QDK_0_0") == 32604
