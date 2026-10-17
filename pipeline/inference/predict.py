"""Hann-blended inference on Q1–Q4 RGBN stacks."""

import numpy as np
import torch
import torch.nn.functional as F
from nodata import NODATA_FILLS, bad_mask, fill_nodata

PATCH, UP = 512, 4
NATIVE = PATCH // UP


def _patch_starts(extent: int, patch: int, stride: int) -> list[int]:
    starts = list(range(0, max(extent - patch, 0) + 1, stride))
    return sorted(set(starts + [max(extent - patch, 0)]))


def hann_window_2d(size: int, device: str) -> torch.Tensor:
    w = torch.hann_window(size, periodic=False, device=device)
    return torch.outer(w, w).clamp_min(1e-3)


def run_cuda(sess, xf, obuf, cuda_sync=True):
    """One ONNX Runtime call on the CUDA provider, input and output bound in place.

    torch fills ``xf`` on its stream and the provider reads it on its own, so with ``cuda_sync``
    the device is synchronized before the call (and the input binding with it) and again after.
    """
    b = xf.shape[0]
    io = sess.io_binding()
    io.bind_input("input", "cuda", 0, np.float32, tuple(xf.shape), xf.data_ptr())
    io.bind_output("logits", "cuda", 0, np.float32, (b, 3, PATCH, PATCH), obuf[:b].data_ptr())
    if cuda_sync:
        torch.cuda.synchronize()
        io.synchronize_inputs()
    sess.run_with_iobinding(io)
    io.synchronize_outputs()
    if cuda_sync:
        torch.cuda.synchronize()
    return obuf[:b]


@torch.inference_mode()
def predict_tile(
    sess,
    arr,
    *,
    batch=64,
    overlap=0.25,
    norm=3000.0,
    dev="cuda",
    obuf_cache=None,
    nodata_fill=None,
    stats=None,
    cuda_sync=True,
):
    """(16,H,W) tile -> ((2, 4H, 4W) uint8 field/boundary scores, n_patches). No I/O.

    ``nodata_fill="window31"`` fills mosaic nodata (-32768) with ``nodata.fill_nodata`` before
    scaling; None passes the raw -32768 to the model. ``stats``, if given, receives
    ``nodata_before`` / ``nodata_after``, the share of pixels with nodata in any channel.

    ``cuda_sync`` (default on) puts a device barrier around every ONNX Runtime call on CUDA:
    torch writes the bound input on its stream and ONNX Runtime's CUDA provider reads it on its
    own. Without the barrier the first batch of a tile could read the previous tile's leftover
    input, and on A100s later batches raced as well. ``cuda_sync=False`` reproduces the
    pre-rerun release.
    """
    if arr.ndim != 3 or arr.shape[0] != 16:
        raise ValueError("expected 16 bands: Q1–Q4 × R,G,B,NIR")
    if batch <= 0 or not 0 <= overlap < 1 or norm <= 0:
        raise ValueError("invalid batch, overlap or normalization")
    if nodata_fill not in (None, *NODATA_FILLS):
        raise ValueError(f"nodata_fill must be one of {NODATA_FILLS}, got {nodata_fill!r}")
    if not np.isfinite(arr).all():
        raise ValueError("input contains nonfinite values")
    if obuf_cache is None:
        obuf_cache = {}
    # one device copy: the caller's array is never modified. Float input is scaled in place;
    # integer input (the int16 mosaics) is scaled into the one float tensor.
    tile_t = torch.from_numpy(arr).to(dev, copy=True)
    if stats is not None:
        stats["nodata_before"] = bad_mask(tile_t).any(0).float().mean().item()
    if nodata_fill is not None:
        tile_t = fill_nodata(tile_t)
    if stats is not None:
        stats["nodata_after"] = bad_mask(tile_t).any(0).float().mean().item()
    tile_t = tile_t.div_(norm) if tile_t.is_floating_point() else tile_t / norm
    _, h, w = tile_t.shape
    pad = (0, max(0, NATIVE - w), 0, max(0, NATIVE - h))
    if any(pad):  # a no-op F.pad still copies the whole tile
        tile_t = F.pad(tile_t, pad, mode="replicate")
        h, w = tile_t.shape[1:]
    out_h, out_w = h * UP, w * UP
    acc = torch.zeros((2, out_h, out_w), device=dev, dtype=torch.float32)
    wsum = torch.zeros((out_h, out_w), device=dev, dtype=torch.float32)
    win = hann_window_2d(PATCH, dev)
    stride = max(1, round(NATIVE * (1.0 - overlap)))
    starts = [
        (r, c) for r in _patch_starts(h, NATIVE, stride) for c in _patch_starts(w, NATIVE, stride)
    ]
    if batch not in obuf_cache:
        obuf_cache[batch] = torch.empty((batch, 3, PATCH, PATCH), device=dev, dtype=torch.float32)
    obuf = obuf_cache[batch]

    def run(xf):
        xf = xf.contiguous()
        if dev == "cpu":
            return torch.from_numpy(sess.run(["logits"], {"input": xf.numpy()})[0])
        return run_cuda(sess, xf, obuf, cuda_sync)

    for i in range(0, len(starts), batch):
        chunk = starts[i : i + batch]
        native = torch.stack([tile_t[:, r : r + NATIVE, c : c + NATIVE] for r, c in chunk])
        x = F.interpolate(native, scale_factor=UP, mode="bilinear", align_corners=False)
        logits = run(x)
        if not torch.isfinite(logits).all():
            raise ValueError("model produced nonfinite logits")
        fb = logits.softmax(1)[:, 1:3]
        for (r, c), score in zip(chunk, fb, strict=True):
            oy, ox = r * UP, c * UP
            acc[:, oy : oy + PATCH, ox : ox + PATCH] += score * win
            wsum[oy : oy + PATCH, ox : ox + PATCH] += win

    acc.div_(wsum).clamp_(0, 1).mul_(255).round_()
    del wsum, tile_t  # free the full-size float32 tensors before the uint8 copy
    out = acc.to(torch.uint8).cpu().numpy()
    return out[:, : arr.shape[1] * UP, : arr.shape[2] * UP], len(starts)
