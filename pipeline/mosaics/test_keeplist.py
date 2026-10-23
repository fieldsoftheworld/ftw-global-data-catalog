import numpy as np
import pytest
import rasterio
import shapely
from affine import Affine
from rasterio.errors import RasterioIOError
from rasterio.warp import transform_bounds

import cropland
import keeplist
from stac import MosaicItem

# 1 km x 1 km of 10 m pixels in UTM 31N.
SIZE, X0, Y0 = 100, 500_000, 5_000_000
BBOX = transform_bounds("EPSG:32631", "EPSG:4326", X0, Y0 - 10 * SIZE, X0 + 10 * SIZE, Y0)


def write_lulc(path, crop_fraction):
    arr = np.ones(SIZE * SIZE, np.uint8)
    arr[: round(SIZE * SIZE * crop_fraction)] = cropland.IOLULC_CROPS_CLASS
    with rasterio.open(
        path, "w", driver="GTiff", width=SIZE, height=SIZE, count=1, dtype="uint8",
        crs="EPSG:32631", transform=Affine(10, 0, X0, 0, -10, Y0),
    ) as ds:
        ds.write(arr.reshape(SIZE, SIZE), 1)
    return str(path)


def raise_http(code):
    def open_(url):
        raise RasterioIOError(f"HTTP response code: {code}")

    return open_


def test_stats_per_year_with_area(tmp_path, monkeypatch):
    urls = {2017: write_lulc(tmp_path / "a.tif", 0.1), 2024: write_lulc(tmp_path / "b.tif", 0.9)}
    monkeypatch.setattr(cropland, "_iolulc_url", lambda tk, year: urls[year])
    stats = cropland.cropland_stats("31TDF_0_0", BBOX, years=(2017, 2024))
    assert stats[2017].fraction == pytest.approx(0.1, abs=0.01)
    assert stats[2024].crop_km2 == pytest.approx(0.9, abs=0.01)


def test_score_one_takes_max_over_years(tmp_path, monkeypatch):
    urls = {y: write_lulc(tmp_path / f"{y}.tif", f) for y, f in ((2017, 0.4), (2020, 0.1), (2024, 0.2))}
    monkeypatch.setattr(cropland, "_iolulc_url", lambda tk, year: urls[year])
    row = keeplist.score_one("31TDF_0_0", BBOX)
    assert row["fraction"] == pytest.approx(0.4, abs=0.01)
    assert row["crop_km2"] == pytest.approx(0.4, abs=0.01)


def test_absent_tile_is_zero(monkeypatch):
    monkeypatch.setattr(cropland.rasterio, "open", raise_http(404))
    row = keeplist.score_one("31TDF_0_0", BBOX)
    assert (row["fraction"], row["crop_km2"], row["error"]) == (0.0, 0.0, "")


def test_transport_error_raises(monkeypatch):
    monkeypatch.setattr(cropland.rasterio, "open", raise_http(503))
    with pytest.raises(RasterioIOError):
        cropland.cropland_stats("31TDF_0_0", BBOX)


def test_unscored_tile_is_kept(monkeypatch):
    monkeypatch.setattr(cropland.rasterio, "open", raise_http(503))
    row = keeplist.score_one("31TDF_0_0", BBOX)
    assert row["fraction"] == 1.0
    assert row["error"].startswith("RasterioIOError:") and "503" in row["error"]


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
