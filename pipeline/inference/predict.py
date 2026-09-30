"""Hann-blended inference on Q1–Q4 RGBN stacks."""

import numpy as np
import torch
import torch.nn.functional as F

PATCH, UP = 512, 4
NATIVE = PATCH // UP


def _patch_starts(extent: int, patch: int, stride: int) -> list[int]:
    starts = list(range(0, max(extent - patch, 0) + 1, stride))
    return sorted(set(starts + [max(extent - patch, 0)]))


def hann_window_2d(size: int, device: str) -> torch.Tensor:
    w = torch.hann_window(size, periodic=False, device=device)
    return torch.outer(w, w).clamp_min(1e-3)


@torch.inference_mode()
def predict_tile(sess, arr, *, batch=64, overlap=0.25, norm=3000.0, dev="cuda", obuf_cache=None):
    """(16,H,W) tile -> ((2, 4H, 4W) uint8 field/boundary scores, n_patches). No I/O."""
    if arr.ndim != 3 or arr.shape[0] != 16:
        raise ValueError("expected 16 bands: Q1–Q4 × R,G,B,NIR")
    if batch <= 0 or not 0 <= overlap < 1 or norm <= 0:
        raise ValueError("invalid batch, overlap or normalization")
    if not np.isfinite(arr).all():
        raise ValueError("input contains nonfinite values")
    if obuf_cache is None:
        obuf_cache = {}
    tile_t = torch.from_numpy(arr).to(dev) / norm
    _, h, w = tile_t.shape
    tile_t = F.pad(tile_t, (0, max(0, NATIVE - w), 0, max(0, NATIVE - h)), mode="replicate")
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
        b = xf.shape[0]
        xf = xf.contiguous()
        if dev == "cpu":
            return torch.from_numpy(sess.run(["logits"], {"input": xf.numpy()})[0])
        torch.cuda.synchronize()
        io = sess.io_binding()
        io.bind_input("input", "cuda", 0, np.float32, tuple(xf.shape), xf.data_ptr())
        io.bind_output("logits", "cuda", 0, np.float32, (b, 3, PATCH, PATCH), obuf[:b].data_ptr())
        sess.run_with_iobinding(io)
        io.synchronize_outputs()
        return obuf[:b]

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

    out = (acc / wsum).clamp_(0, 1).mul_(255).round_().to(torch.uint8).cpu().numpy()
    return out[:, : arr.shape[1] * UP, : arr.shape[2] * UP], len(starts)
