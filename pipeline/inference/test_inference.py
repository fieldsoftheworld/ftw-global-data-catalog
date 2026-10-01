import numpy as np
import pytest
import rasterio
import torch
from affine import Affine
from predict import predict_tile, _patch_starts
from run import (
    BAND_DESCRIPTIONS,
    INPUT_BANDS,
    current,
    fingerprint,
    output_tags,
    read_stack,
    write_score,
)
from torch.utils._python_dispatch import TorchDispatchMode


class ConstantSession:
    def run(self, names, feed):
        shape = feed["input"].shape
        out = np.zeros((shape[0], 3, 512, 512), np.float32)
        out[:, 1] = np.log(2)
        return [out]


class AllocTrace(TorchDispatchMode):
    """Record the shapes of fresh (non-aliasing) float32 allocations."""

    def __init__(self):
        self.fresh = []

    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        kwargs = kwargs or {}
        flat = [
            item
            for arg in (*args, *kwargs.values())
            for item in (arg if isinstance(arg, (list, tuple)) else [arg])
        ]
        before = {t.untyped_storage().data_ptr() for t in flat if isinstance(t, torch.Tensor)}
        out = func(*args, **kwargs)
        for t in out if isinstance(out, (list, tuple)) else [out]:
            if (
                isinstance(t, torch.Tensor)
                and t.dtype == torch.float32
                and t.untyped_storage().data_ptr() not in before
            ):
                self.fresh.append(tuple(t.shape))
        return out


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


def test_blend_allocates_one_accumulator():
    """A second full-size float32 tile would OOM a real 40,032^2 output."""
    torch.set_num_threads(1)
    h, w = 143, 201
    arr = np.full((16, h, w), 1500, np.float32)
    with AllocTrace() as trace:
        result, _ = predict_tile(ConstantSession(), arr, dev="cpu", batch=3)
    scores, weights = (2, h * 4, w * 4), (h * 4, w * 4)
    assert result.shape == scores
    assert trace.fresh.count(scores) == 1, f"extra full-size tensors: {trace.fresh}"
    assert trace.fresh.count(weights) == 1, f"extra weight tensors: {trace.fresh}"
    np.testing.assert_array_equal(arr, 1500)


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


def test_resume_and_tags_track_the_provider(tmp_path):
    """A CPU-made COG must not satisfy the skip test for a CUDA run."""
    src = tmp_path / "stack.tif"
    src.write_bytes(b"stack")
    cpu = fingerprint(src, "hash", 64, 0.25, 3000.0, "CPUExecutionProvider")
    cuda = fingerprint(src, "hash", 64, 0.25, 3000.0, "CUDAExecutionProvider")
    assert cpu != cuda
    tags = output_tags({"year": "2025"}, "hash", cpu, 3000.0, 0.25, "CPUExecutionProvider")
    dst = tmp_path / "score.tif"
    tr = Affine(2.5, 0, 500000, 0, -2.5, 1000000)
    write_score(dst, np.zeros((2, 64, 128), np.uint8), "EPSG:32631", tr, tags)
    assert current(dst, cpu)
    assert not current(dst, cuda)
    with rasterio.open(dst) as ds:
        written = ds.tags()
    assert written["execution_provider"] == "CPUExecutionProvider"
    assert written["model_sha256"] == "hash"
    assert written["year"] == "2025"


def stack(path, *, descriptions=BAND_DESCRIPTIONS, tags=None):
    """A minimal 16-band 10 m north-up stack, optionally described and/or tagged."""
    tr = Affine(10, 0, 500000, 0, -10, 1000000)
    with rasterio.open(
        path, "w", driver="GTiff", height=8, width=8, count=16,
        dtype="uint16", crs="EPSG:32631", transform=tr,
    ) as ds:
        ds.write(np.ones((16, 8, 8), np.uint16))
        for i, name in enumerate(descriptions or (), 1):
            ds.set_band_description(i, name)
        if tags:
            ds.update_tags(**tags)
    return path


def test_input_bands_matches_the_mosaic_pipeline():
    """One spelling, shared with pipeline/mosaics/download.py."""
    assert INPUT_BANDS == "Q1,Q2,Q3,Q4 x B04,B03,B02,B08"
    assert BAND_DESCRIPTIONS[:5] == ("Q1_B04", "Q1_B03", "Q1_B02", "Q1_B08", "Q2_B04")
    assert len(BAND_DESCRIPTIONS) == 16


def test_band_order_accepted_by_descriptions(tmp_path):
    arr, transform, crs, tags = read_stack(stack(tmp_path / "described.tif"))
    assert arr.shape == (16, 8, 8) and arr.dtype == np.float32
    assert transform.a == 10 and crs is not None
    assert "input_bands" not in tags
    out = output_tags(tags, "h", "fp", 3000.0, 0.25, "CPUExecutionProvider")
    assert out["input_bands"] == INPUT_BANDS


def test_band_order_accepted_by_tag(tmp_path):
    src = stack(tmp_path / "tagged.tif", descriptions=None, tags={"input_bands": INPUT_BANDS})
    _, _, _, tags = read_stack(src)
    assert tags["input_bands"] == INPUT_BANDS


def test_band_order_refused_when_unconfirmed(tmp_path):
    """Blank descriptions and no tag must not be treated as the right order."""
    with pytest.raises(ValueError, match="band order unconfirmed"):
        read_stack(stack(tmp_path / "blank.tif", descriptions=None))


def test_band_order_refused_on_conflicting_claims(tmp_path):
    wrong = ("Q1_B02", "Q1_B03", "Q1_B04", "Q1_B08") + BAND_DESCRIPTIONS[4:]
    with pytest.raises(ValueError, match="band descriptions"):
        read_stack(stack(tmp_path / "swapped.tif", descriptions=wrong))
    mislabelled = {"input_bands": "Q1,Q2,Q3,Q4 x B02,B03,B04,B08"}
    with pytest.raises(ValueError, match="input_bands tag"):
        read_stack(stack(tmp_path / "mislabelled.tif", tags=mislabelled))
