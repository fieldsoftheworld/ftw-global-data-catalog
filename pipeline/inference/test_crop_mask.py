import numpy as np
import rasterio
from affine import Affine

import crop_mask
from crop_mask import CELL, apply_mask, crop_grid, mask_tile, near_crop
from run import write_score

X0, Y0 = 500_000, 5_000_000  # UTM 31N, band T


def write_lulc(path, arr):
    with rasterio.open(
        path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
        dtype="uint8", crs="EPSG:32631", transform=Affine(10, 0, X0, 0, -10, Y0),
    ) as ds:
        ds.write(arr.astype(np.uint8), 1)
    return str(path)


def test_apply_mask_zeroes_whole_cells_including_a_partial_edge():
    scores = np.full((2, 2 * CELL, 2 * CELL + 8), 200, np.uint8)
    keep = np.array([[True, False, True], [False, True, False]])
    apply_mask(scores, keep)
    assert (scores[:, :CELL, CELL : 2 * CELL] == 0).all()
    assert (scores[:, :CELL, :CELL] == 200).all()
    assert (scores[:, CELL:, 2 * CELL :] == 0).all()


def test_near_crop_buffers_and_masks_a_tile_without_crop():
    crop = np.zeros((9, 9), bool)
    crop[4, 4] = True
    keep = near_crop(crop, 40.0, 80.0)
    assert keep[4, 2] and keep[4, 6] and not keep[4, 7] and not keep[0, 0]
    assert not near_crop(np.zeros((3, 3), bool), 40.0, 1000.0).any()


def test_crop_grid_maps_lulc_crop_onto_the_40m_grid(tmp_path, monkeypatch):
    lulc = np.ones((200, 200))
    lulc[:, :100] = 5  # crop in the western 1 km
    path = write_lulc(tmp_path / "31T.tif", lulc)
    monkeypatch.setattr(crop_mask, "_iolulc_url", lambda gzd, year: path)
    crop = crop_grid("31TDF_0_0", "EPSG:32631", Affine(40, 0, X0, 0, -40, Y0), (50, 50))
    assert crop[:, :24].all() and not crop[:, 26:].any()


def test_mask_tile_zeroes_far_fields_and_tags_the_buffer(tmp_path, monkeypatch):
    lulc = np.ones((200, 200))
    lulc[:, :20] = 5  # crop only in the western 200 m
    path = write_lulc(tmp_path / "31T.tif", lulc)
    monkeypatch.setattr(crop_mask, "_iolulc_url", lambda gzd, year: path)
    src, dst = tmp_path / "src.tif", tmp_path / "out" / "dst.tif"
    scores = np.full((2, 800, 800), 200, np.uint8)  # 2 km of 2.5 m fields
    write_score(src, scores, "EPSG:32631", Affine(2.5, 0, X0, 0, -2.5, Y0), {"tile_key": "31TDF_0_0"})
    share = mask_tile(src, dst, "31TDF_0_0", 200.0)
    with rasterio.open(dst) as ds:
        out = ds.read(1)
        assert ds.tags()["crop_mask_buffer_m"] == "200.0"
    assert out[:, :160].min() == 200  # within 400 m of the west edge
    assert out[:, 240:].max() == 0
    assert 0.6 < share < 0.85
