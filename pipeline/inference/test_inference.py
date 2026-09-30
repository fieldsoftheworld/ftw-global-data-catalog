import numpy as np
import rasterio
import torch
from affine import Affine
from predict import predict_tile, _patch_starts
from run import write_score, current


class ConstantSession:
    def run(self, names, feed):
        shape = feed["input"].shape
        out = np.zeros((shape[0], 3, 512, 512), np.float32)
        out[:, 1] = np.log(2)
        return [out]


def test_blend_small_and_edge_patches():
    torch.set_num_threads(1)
    for h, w in [(37, 91), (143, 201)]:
        result, count = predict_tile(
            ConstantSession(), np.zeros((16, h, w), np.float32), dev="cpu", batch=3
        )
        assert result.shape == (2, h * 4, w * 4)
        assert count > 0
        np.testing.assert_allclose(result[0], 127.5, atol=0.5)
        np.testing.assert_array_equal(result[1], 64)
        assert _patch_starts(w, 128, 96)[-1] == max(w - 128, 0)


def test_cog_contract(tmp_path):
    dst = tmp_path / "score.tif"
    data = np.stack([np.full((64, 128), 128, np.uint8), np.full((64, 128), 64, np.uint8)])
    tr = Affine(2.5, 0, 500000, 0, -2.5, 1000000)
    write_score(dst, data, "EPSG:32631", tr, {"inference_fingerprint": "fixture"})
    with rasterio.open(dst) as ds:
        np.testing.assert_array_equal(ds.read(), data)
        assert ds.transform == tr
        assert ds.scales == (1 / 255, 1 / 255)
        assert ds.descriptions == ("field", "boundary")
        assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    assert current(dst, "fixture")
    assert not current(dst, "changed")
