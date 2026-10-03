"""frac_nodata_* must flag the swath edges it exists to flag."""

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import context

ND = context.NODATA
H = W = 640  # 160 QA cells per axis


def _ragged_swath(path: Path) -> np.ndarray:
    """A B04-like int16 mosaic with a ragged nodata swath edge and CUBIC overviews.

    Cubic overviews are what the real COGs carry, and they are what a decimated
    read is served from.
    """
    rng = np.random.default_rng(0)
    data = np.full((H, W), 1200, np.int16)
    for r in range(H):
        data[r, : int(W * 0.45 + rng.integers(-40, 40))] = ND
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=H,
        width=W,
        count=1,
        dtype="int16",
        nodata=ND,
        crs="EPSG:32631",
        transform=rasterio.Affine(10, 0, 400000, 0, -10, 5400000),
        tiled=True,
        blockxsize=128,
        blockysize=128,
    ) as ds:
        ds.write(data, 1)
        ds.build_overviews([2, 4, 8], Resampling.cubic)
    return data


def _truth(data: np.ndarray, h40: int, w40: int):
    "Ground truth per 40 m cell: (any nodata pixel, only-some-pixels nodata)."
    blocks = data.reshape(h40, 4, w40, 4) == ND
    any_nd = blocks.any(axis=(1, 3))
    return any_nd, any_nd & ~blocks.all(axis=(1, 3))


def test_partial_nodata_cells_are_all_flagged(tmp_path):
    """Every 40 m cell containing any nodata pixel must be flagged.

    The decimated-value comparison missed 494 of 2,086 partially-nodata cells
    (24%) on this fixture: the overview interpolates before the nearest subsample,
    so a cell on the swath edge never equals -32768 exactly. The attribute read
    ~0 along precisely the mosaic edges it exists to flag.
    """
    src = tmp_path / "b04.tif"
    data = _ragged_swath(src)
    h40, w40 = H // 4, W // 4
    any_nd, partial = _truth(data, h40, w40)
    assert partial.sum() > 1000, "fixture must have plenty of partial cells"

    with rasterio.open(src) as ds:
        got = context.nodata_cells(ds, h40, w40)
        # the old path, for the record
        old = ds.read(1, out_shape=(h40, w40), resampling=Resampling.nearest) == ND

    assert np.array_equal(got, any_nd), f"{(any_nd & ~got).sum()} cells missed"
    assert (partial & ~old).sum() > 0, "the decimated-value path really does miss cells"


def test_reduction_is_stripe_independent(tmp_path):
    src = tmp_path / "b04.tif"
    _ragged_swath(src)
    h40, w40 = H // 4, W // 4
    with rasterio.open(src) as ds:
        a = context.nodata_cells(ds, h40, w40, stripe=512)
        b = context.nodata_cells(ds, h40, w40, stripe=7)
    assert np.array_equal(a, b)


def test_mismatched_source_grid_fails_loudly(tmp_path):
    src = tmp_path / "b04.tif"
    _ragged_swath(src)
    with rasterio.open(src) as ds:
        with pytest.raises(ValueError, match=r"is 640x640, expected 640x644"):
            context.nodata_cells(ds, 160, 161)


def test_read_masks_at_reduced_resolution_is_also_defeated(tmp_path):
    "Pins why the fix is a full-resolution reduction and not read_masks(out_shape=...)."
    src = tmp_path / "b04.tif"
    data = _ragged_swath(src)
    h40, w40 = H // 4, W // 4
    _, partial = _truth(data, h40, w40)
    with rasterio.open(src) as ds:
        valid = ds.read_masks(1, out_shape=(h40, w40), resampling=Resampling.average) / 255.0
    assert (partial & ~(valid < 1.0)).sum() > 0
