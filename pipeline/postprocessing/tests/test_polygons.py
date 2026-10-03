"""Coverage simplification must not invent gaps, overlaps or crashes."""

import sys
from pathlib import Path

import numpy as np
import pytest
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polygons import simplify_coverage


def _shared_edge_pair():
    "Two parcels sharing one wiggly 20-vertex edge, plus a third that overlaps one."
    ys = np.linspace(0, 100, 20)
    xs = 50 + np.sin(ys / 7.0) * 3.0
    shared = list(zip(xs, ys))
    left = shapely.Polygon([(0, 0)] + shared + [(0, 100)])
    right = shapely.Polygon([(100, 0), (100, 100)] + shared[::-1])
    overlapping = shapely.box(90, 40, 110, 60)
    return left, right, overlapping


def test_shared_edge_stays_shared_with_an_invalid_coverage_member():
    """0 m2 gap and 0 m2 overlap across a shared simplified edge.

    Splitting the coverage into a coverage_simplify half and a Douglas-Peucker
    half at min(tol, 1.2) m measured 26.06 m2 of overlap and 15.29 m2 of gap on
    this fixture -- both under fiboa's 100 m2 seam-repair floor, so they would
    have shipped.
    """
    left, right, overlapping = _shared_edge_pair()
    arr = np.array([left, right, overlapping], dtype=object)
    assert (~shapely.is_empty(shapely.coverage_invalid_edges(arr))).any(), "fixture must be dirty"

    out = simplify_coverage(arr, 5.0, label="31UFS")
    simp_left, simp_right = out[0], out[1]
    assert simp_left.intersection(simp_right).area == pytest.approx(0.0, abs=1e-9)
    gap = shapely.union_all([left, right]).difference(shapely.union_all([simp_left, simp_right]))
    assert gap.area == pytest.approx(0.0, abs=1e-9)
    assert shapely.is_valid(out).all()


def test_simplification_actually_happened():
    "Control: the 0/0 result above must not come from skipping simplification."
    left, right, overlapping = _shared_edge_pair()
    arr = np.array([left, right, overlapping], dtype=object)
    before = shapely.get_num_coordinates(arr).sum()
    after = shapely.get_num_coordinates(simplify_coverage(arr, 5.0)).sum()
    assert after < before, (before, after)


@pytest.mark.parametrize("pos", [0, 1, 2])
def test_none_geometry_is_rejected_by_index(pos):
    """None in any position must raise, naming the tile and the index.

    coverage_invalid_edges does not keep None in place: [a, None, b] returns
    [EMPTY, EMPTY, None], so a mask taken from it flags a real polygon as invalid
    coverage and feeds the None to coverage_simplify, which died with "cannot
    reshape array of size 2 into shape (3,)".
    """
    geoms = [shapely.box(0, 0, 10, 10), shapely.box(10, 0, 20, 10), shapely.box(20, 0, 30, 10)]
    geoms[pos] = None
    with pytest.raises(ValueError, match=rf"31UFS: 1 missing geometry at index {pos}"):
        simplify_coverage(np.array(geoms, dtype=object), 5.0, label="31UFS")


def test_coverage_invalid_edges_does_not_keep_none_in_place():
    "Pins the shapely behaviour the rejection above exists for."
    a, b = shapely.box(0, 0, 10, 10), shapely.box(10, 0, 20, 10)
    for arr in (
        np.array([None, a, b], dtype=object),
        np.array([a, None, b], dtype=object),
        np.array([a, b, None], dtype=object),
    ):
        res = shapely.coverage_invalid_edges(arr)
        assert res[2] is None, "the None always lands last, wherever it came from"


def test_none_is_rejected_even_at_zero_tolerance():
    "A None must never reach the caller's WKB encoder, simplified or not."
    with pytest.raises(ValueError, match="missing geometry"):
        simplify_coverage(np.array([shapely.box(0, 0, 1, 1), None], dtype=object), 0.0)


def test_empty_input_is_returned_unchanged():
    arr = np.array([], dtype=object)
    assert len(simplify_coverage(arr, 5.0)) == 0


def test_output_is_always_polygonal(capsys):
    "A sliver that repairs to nothing polygonal must not become a LineString."
    sliver = shapely.Polygon([(0, 0), (10, 0), (0, 0)])
    out = simplify_coverage(np.array([shapely.box(50, 50, 60, 60), sliver], dtype=object), 5.0)
    assert set(shapely.get_type_id(out)) <= {3, 6}, [g.geom_type for g in out]
