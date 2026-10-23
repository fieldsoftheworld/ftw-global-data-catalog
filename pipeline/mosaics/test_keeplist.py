import numpy as np
import pytest
import rasterio
import shapely
from affine import Affine
from rasterio.errors import RasterioIOError

import cropland
import keeplist
from stac import MosaicItem

BBOX = (0.0, 0.0, 1.0, 1.0)


def write_lulc(path, crop_fraction, size=10):
    arr = np.ones(size * size, np.uint8)
    arr[: round(size * size * crop_fraction)] = cropland.IOLULC_CROPS_CLASS
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1, dtype="uint8",
        crs="EPSG:4326", transform=Affine(1 / size, 0, 0, 0, -1 / size, 1),
    ) as ds:
        ds.write(arr.reshape(size, size), 1)
    return str(path)


def raise_http(code):
    def open_(url):
        raise RasterioIOError(f"HTTP response code: {code}")

    return open_


def test_fraction_is_max_over_years(tmp_path, monkeypatch):
    urls = {2017: write_lulc(tmp_path / "a.tif", 0.1), 2024: write_lulc(tmp_path / "b.tif", 0.9)}
    monkeypatch.setattr(cropland, "_iolulc_url", lambda tk, year: urls[year])
    stat = cropland.cropland_fraction("15TVG_0_0", BBOX, years=(2017, 2024))
    assert stat.fraction == pytest.approx(0.9, abs=0.01)


def test_absent_tile_is_zero(monkeypatch):
    monkeypatch.setattr(cropland.rasterio, "open", raise_http(404))
    assert cropland.cropland_fraction("15TVG_0_0", BBOX).fraction == 0.0


def test_transport_error_raises(monkeypatch):
    monkeypatch.setattr(cropland.rasterio, "open", raise_http(503))
    with pytest.raises(RasterioIOError):
        cropland.cropland_fraction("15TVG_0_0", BBOX)


def test_unscored_tile_is_kept(monkeypatch):
    monkeypatch.setattr(cropland.rasterio, "open", raise_http(503))
    row = keeplist.score_one("15TVG_0_0", BBOX)
    assert (row["fraction"], row["error"]) == (1.0, "RasterioIOError")


def test_antimeridian_tile_scores_largest_part():
    small = shapely.box(179.0, 60.0, 179.5, 60.5)
    large = shapely.box(-179.5, 60.0, -179.0, 62.0)
    item = MosaicItem(
        item_id="Sentinel-2_mosaic_2024_Q3_01WCP_0_0",
        year=2024,
        quarter="Q3",
        bbox=(-180.0, 60.0, 180.0, 62.0),
        assets={},
        geometry=shapely.geometry.mapping(shapely.MultiPolygon([small, large])),
    )
    row = keeplist.footprint_row(item)
    assert row["antimeridian"]
    assert (row["score_west"], row["score_east"]) == (-179.5, -179.0)
