"""The window31 nodata fill, the CUDA stream barrier and the pinned run settings."""

import json

import numpy as np
import pytest
import torch
from nodata import NODATA, fill_nodata
from predict import NATIVE, PATCH, UP, predict_tile, run_cuda
from run import (
    CUDA_OPTIONS,
    cuda_options,
    fingerprint,
    options_applied,
    output_tags,
)


class ConstantSession:
    """CPU stand-in for the session: logits where the field wins everywhere."""

    def run(self, names, feed):
        n = feed["input"].shape[0]
        out = np.zeros((n, 3, PATCH, PATCH), np.float32)
        out[:, 1] = 5.0
        out[:, 2] = -5.0
        return [out]


def stack(size=64, seed=0, dtype=torch.int16):
    g = torch.Generator().manual_seed(seed)
    return torch.randint(500, 2500, (16, size, size), generator=g).to(dtype)


def window_mean(x, c, r, col, k=31):
    """Valid mean over a k x k window (tile borders and nodata count as invalid) and its valid share."""
    h = k // 2
    win = x[c, max(0, r - h) : r + h + 1, max(0, col - h) : col + h + 1]
    ok = win != NODATA
    return win[ok].double().mean().item(), ok.sum().item() / (k * k)


def test_clean_input_is_returned_as_is():
    x = stack()
    assert fill_nodata(x) is x


def test_only_nodata_pixels_change():
    x = stack()
    x[0, 20:30, 20:30] = NODATA
    y = fill_nodata(x)
    keep = x != NODATA
    assert torch.equal(y[keep], x[keep])
    assert (y != NODATA).all()
    assert y.dtype == torch.int16


def test_window_mean_near_a_hole_edge():
    x = stack()
    x[1, :, 30:] = NODATA  # right half of one channel is a hole; pixels near x=30 see 50% valid
    y = fill_nodata(x)
    mean, share = window_mean(x, 1, 32, 31)
    assert share >= 0.30
    assert abs(y[1, 32, 31].item() - round(mean)) <= 1


def test_window_mean_inside_a_small_hole():
    x = stack()
    x[5, 30:33, 30:33] = NODATA
    y = fill_nodata(x)
    mean, share = window_mean(x, 5, 31, 31)
    assert share > 0.9
    assert abs(y[5, 31, 31].item() - round(mean)) <= 1


def test_large_hole_falls_back_to_the_other_quarters():
    x = stack(size=96)
    x[0, :, :] = NODATA  # whole Q1 red channel: no valid pixel in any window
    x[0, 10:12, 10:12] = 1234  # a few valid pixels far from the probe
    y = fill_nodata(x)
    others = x[[4, 8, 12], 60, 60].double().mean().item()  # Q2-Q4, same band
    assert abs(y[0, 60, 60].item() - round(others)) <= 1


def test_all_quarters_nodata_uses_the_tile_quarter_band_median():
    x = stack(size=96)
    for c in (0, 4, 8, 12):
        x[c, 40:90, 40:90] = NODATA  # the same big hole in all four quarters of band 0
    y = fill_nodata(x)
    for c in (0, 4, 8, 12):
        valid = x[c][x[c] != NODATA].float()
        assert abs(y[c, 65, 65].item() - valid.median().item()) <= 1


def test_non_finite_floats_count_as_nodata():
    x = stack().float()
    x[2, 10:14, 10:14] = float("nan")
    y = fill_nodata(x)
    assert torch.isfinite(y).all()
    assert torch.equal(y[x.isfinite()], x[x.isfinite()])


def test_predict_tile_fills_and_reports_stats():
    size = 2 * NATIVE
    arr = np.full((16, size, size), 1000, dtype=np.int16)
    arr[0, :10, :10] = NODATA
    stats = {}
    out, _ = predict_tile(
        ConstantSession(), arr, batch=2, dev="cpu", nodata_fill="window31", stats=stats
    )
    assert out.shape == (2, size * UP, size * UP)
    assert stats["nodata_before"] > 0
    assert stats["nodata_after"] == 0
    assert (arr[0, :10, :10] == NODATA).all(), "the caller's array must not be modified"


def test_predict_tile_without_fill_keeps_the_raw_nodata():
    size = 2 * NATIVE
    arr = np.full((16, size, size), 1000, dtype=np.int16)
    arr[0, :10, :10] = NODATA
    stats = {}
    predict_tile(ConstantSession(), arr, batch=2, dev="cpu", stats=stats)
    assert stats["nodata_before"] == stats["nodata_after"] > 0


def test_int16_and_float32_stacks_give_the_same_scores_without_nodata():
    size = 2 * NATIVE
    arr = np.random.default_rng(1).integers(500, 2500, (16, size, size)).astype(np.int16)
    a, _ = predict_tile(ConstantSession(), arr, batch=2, dev="cpu")
    b, _ = predict_tile(ConstantSession(), arr.astype(np.float32), batch=2, dev="cpu")
    np.testing.assert_array_equal(a, b)


def test_predict_tile_rejects_an_unknown_fill():
    with pytest.raises(ValueError, match="nodata_fill"):
        predict_tile(
            ConstantSession(), np.zeros((16, NATIVE, NATIVE), np.int16), dev="cpu",
            nodata_fill="bogus",
        )


class Binding:
    def __init__(self, log):
        self.log = log

    def bind_input(self, *args):
        self.log.append("bind_in")

    def bind_output(self, *args):
        self.log.append("bind_out")

    def synchronize_inputs(self):
        self.log.append("sync_in")

    def synchronize_outputs(self):
        self.log.append("sync_out")


class LoggingSession:
    def __init__(self):
        self.log = []

    def io_binding(self):
        return Binding(self.log)

    def run_with_iobinding(self, io):
        self.log.append("run")


@pytest.mark.parametrize(
    ("cuda_sync", "expected"),
    [
        (True, ["bind_in", "bind_out", "device_sync", "sync_in", "run", "sync_out", "device_sync"]),
        (False, ["bind_in", "bind_out", "run", "sync_out"]),
    ],
)
def test_cuda_barrier_surrounds_every_provider_call(monkeypatch, cuda_sync, expected):
    sess = LoggingSession()
    monkeypatch.setattr(torch.cuda, "synchronize", lambda *a: sess.log.append("device_sync"))
    obuf = torch.empty((2, 3, PATCH, PATCH))
    run_cuda(sess, torch.zeros((2, 16, PATCH, PATCH)), obuf, cuda_sync)
    assert sess.log == expected


def test_cuda_options_pin_exhaustive_search_and_tf32_off():
    assert CUDA_OPTIONS == {"cudnn_conv_algo_search": "EXHAUSTIVE", "use_tf32": "0"}
    assert cuda_options() == CUDA_OPTIONS
    assert cuda_options(tf32=True)["use_tf32"] == "1"


class OptionSession:
    def __init__(self, options):
        self.options = options

    def get_provider_options(self):
        return {"CUDAExecutionProvider": self.options}


def test_options_applied_catches_a_provider_that_kept_tf32_on():
    ok = OptionSession({"use_tf32": "0", "cudnn_conv_algo_search": "EXHAUSTIVE"})
    assert options_applied(ok, "cuda") is None
    assert "use_tf32" in options_applied(OptionSession({"use_tf32": "1"}), "cuda")
    assert options_applied(OptionSession({"use_tf32": "1"}), "cuda", tf32=True) is None
    assert options_applied(object(), "cpu") is None


def test_settings_are_part_of_the_resume_fingerprint_and_the_tags(tmp_path):
    src = tmp_path / "stack.tif"
    src.write_bytes(b"stack")
    base = fingerprint(src, "h", 64, 0.25, 3000.0, "CUDAExecutionProvider")
    for change in ({"nodata_fill": "window31"}, {"tf32": True}, {"cuda_sync": False}):
        assert fingerprint(src, "h", 64, 0.25, 3000.0, "CUDAExecutionProvider", **change) != base
    assert json.loads(base)[-3:] == [None, False, True]
    tags = output_tags(
        {}, "h", base, 3000.0, 0.25, "CUDAExecutionProvider", nodata_fill="window31"
    )
    assert (tags["nodata_fill"], tags["cuda_sync"], tags["tf32"]) == ("window31", "1", "0")
    assert "nodata_fill" not in output_tags({}, "h", base, 3000.0, 0.25, "CPUExecutionProvider")


def test_main_defaults_to_the_published_settings(tmp_path, monkeypatch):
    from test_inference import _fake_run  # noqa: PLC0415

    import run

    seen = {}
    real = run.output_tags
    monkeypatch.setattr(
        run, "output_tags", lambda *a, **k: seen.update(k) or real(*a, **k)
    )
    _fake_run(monkeypatch, tmp_path, [])
    assert seen["nodata_fill"] == "window31"
    assert seen["tf32"] is False
    assert seen["cuda_sync"] is True
    _fake_run(monkeypatch, tmp_path / "again", ["--nodata-fill", "none", "--no-cuda-sync"])
    assert seen["nodata_fill"] is None
    assert seen["cuda_sync"] is False


