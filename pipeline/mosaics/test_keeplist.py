"""Cropland keep-list: io-lulc scoring, absent-vs-transient classification, and the
pure row-building helpers in keeplist.py."""

import shapely
from affine import Affine
from rasterio.errors import RasterioIOError

import cropland
import keeplist
import rasterio
from keeplist import footprint_row, keep_tiles
from stac import MosaicItem

import numpy as np
import pytest


def write_lulc(tmp_path, name: str, crop_fraction: float, size: int = 10):
    "A size x size uint8 raster over EPSG:4326 [0,1]x[0,1], crop_fraction class 5, rest class 1."
    path = tmp_path / name
    n_crop = round(size * size * crop_fraction)
    arr = np.full(size * size, 1, np.uint8)
    arr[:n_crop] = cropland.IOLULC_CROPS_CLASS
    arr = arr.reshape(size, size)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=Affine(1.0 / size, 0, 0, 0, -1.0 / size, 1.0),
    ) as ds:
        ds.write(arr, 1)
    return path


BBOX = (0.0, 0.0, 1.0, 1.0)


def test_cropland_fraction_is_the_max_over_years(tmp_path, monkeypatch):
    low = write_lulc(tmp_path, "low.tif", 0.1)
    high = write_lulc(tmp_path, "high.tif", 0.9)
    by_year = {2017: str(low), 2024: str(high)}
    monkeypatch.setattr(cropland, "_iolulc_url", lambda tk, year: by_year[year])
    stat = cropland.cropland_fraction_iolulc("15TVG_0_0", BBOX, years=(2017, 2024))
    # Resampling a 10x10 source up to the 512x512 scoring grid shifts the boundary
    # pixel a little; only the ordering (max over years) and rough magnitude matter.
    assert stat.fraction == pytest.approx(0.9, abs=0.01)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("... HTTP response code: 404 ...", True),
        ("... HTTP response code: 410 ...", True),
        ("... HTTP response code: 503 ...", False),
        ("... HTTP response code: 403 ...", False),
        ("Connection timed out", False),
    ],
)
def test_is_absent_classifies_only_404_410(message, expected):
    assert cropland._is_absent(RasterioIOError(message)) is expected


def test_absent_tile_scores_zero_cropland_without_raising(tmp_path, monkeypatch):
    def boom(url):
        raise RasterioIOError("no such object (HTTP response code: 404)")

    monkeypatch.setattr(cropland.rasterio, "open", boom)
    stat = cropland.cropland_fraction_iolulc("15TVG_0_0", BBOX)
    assert stat.fraction == 0.0


def test_transient_fetch_error_is_not_swallowed(monkeypatch):
    def boom(url):
        raise RasterioIOError("server error (HTTP response code: 503)")

    monkeypatch.setattr(cropland.rasterio, "open", boom)
    with pytest.raises(RasterioIOError):
        cropland.cropland_fraction_iolulc("15TVG_0_0", BBOX)


def mosaic_item(geometry, bbox):
    return MosaicItem(
        item_id="S2_10m_mosaic_2024_Q3_15TVG_0_0",
        year=2024,
        quarter="Q3",
        bbox=bbox,
        assets={},
        geometry=geometry,
    )


def test_footprint_row_for_an_ordinary_tile():
    geom = shapely.geometry.mapping(shapely.box(5.0, 51.0, 5.1, 51.1))
    item = mosaic_item(geom, (5.0, 51.0, 5.1, 51.1))
    row = footprint_row("31UFS_0_0", item)
    assert row["antimeridian"] is False
    assert (row["score_west"], row["score_east"]) == (5.0, 5.1)
    assert shapely.from_wkb(row["geometry"]).equals(shapely.box(5.0, 51.0, 5.1, 51.1))


def test_footprint_row_scores_the_largest_part_of_a_dateline_tile():
    # An antimeridian-crossing tile's STAC bbox is the enclosing (near-)360-degree
    # box, but its two MultiPolygon parts do not themselves span the globe: the
    # small part sits at +179, the large one at -179.
    small = shapely.box(179.0, 60.0, 179.5, 60.5)
    large = shapely.box(-179.5, 60.0, -179.0, 62.0)
    item = mosaic_item(shapely.geometry.mapping(shapely.MultiPolygon([small, large])), (-180.0, 60.0, 180.0, 62.0))
    row = footprint_row("01WCP_0_0", item)
    assert row["antimeridian"] is True
    assert (row["score_west"], row["score_east"]) == pytest.approx((-179.5, -179.0))


def test_keep_tiles_filters_on_strict_threshold_and_sorts():
    rows = [
        {"tile_key": "b", "fraction": 0.01},
        {"tile_key": "a", "fraction": 0.5},
        {"tile_key": "c", "fraction": 0.0},
    ]
    assert keep_tiles(rows, 0.01) == ["a"]
    assert keep_tiles(rows, 0.0) == ["a", "b"]


def test_score_one_keeps_an_unscoreable_tile(monkeypatch):
    def boom(tile_key, bbox, **kwargs):
        raise RasterioIOError("server error (HTTP response code: 503)")

    monkeypatch.setattr(keeplist, "cropland_fraction_iolulc", boom)
    row = keeplist._score_one("15TVG_0_0", BBOX)
    assert row["fraction"] == 1.0
    assert row["error"] == "RasterioIOError"


def test_score_one_reports_a_scored_tile(monkeypatch):
    monkeypatch.setattr(
        keeplist,
        "cropland_fraction_iolulc",
        lambda tile_key, bbox, **kwargs: cropland.CroplandStat(cropland_px=3, valid_px=10),
    )
    row = keeplist._score_one("15TVG_0_0", BBOX)
    assert row == {"tile_key": "15TVG_0_0", "fraction": 0.3, "valid_px": 10, "error": ""}
