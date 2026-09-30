import json
import numpy as np
import pytest
import rasterio
from affine import Affine
from download import stack_bands


def test_band_order_and_grid_validation(tmp_path):
    paths = []
    for i in range(16):
        path = tmp_path / f"band{i}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=40,
            height=30,
            count=1,
            dtype="int16",
            crs="EPSG:32631",
            transform=Affine(10, 0, 500000, 0, -10, 1000000),
        ) as ds:
            ds.write(np.full((30, 40), i, np.int16), 1)
        paths.append(path)
    dst = tmp_path / "stack.tif"
    stack_bands(paths, dst, {"source_items": json.dumps(["Q1", "Q2", "Q3", "Q4"])})
    with rasterio.open(dst) as ds:
        assert ds.count == 16 and ds.descriptions[0] == "Q1_B04"
        assert ds.descriptions[-1] == "Q4_B08"
        np.testing.assert_array_equal(ds.read()[:, 0, 0], np.arange(16))
    with rasterio.open(paths[-1], "r+") as ds:
        ds.transform = Affine(10, 0, 500010, 0, -10, 1000000)
    with pytest.raises(ValueError, match="one grid"):
        stack_bands(paths, tmp_path / "bad.tif", {})
