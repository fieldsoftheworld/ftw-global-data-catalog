"""Fill mosaic nodata before inference (the ``window31`` rule the 2026-10 rerun used).

The CDSE mosaics mark missing observations as -32768. Passed to the model raw, that value
becomes -10.9 after the /3000 scaling and the network turns it into spurious "field"
probability inside every nodata hole, most of all in the sparse 2017-2019 mosaics. The fill
replaces each nodata pixel before scaling:

1. the mean of the same quarter and band over a ``FILL_WINDOW`` px square, when at least
   ``FILL_MIN_VALID`` of that window is valid (tile borders count as invalid),
2. else the mean of the other quarters' valid values at that pixel,
3. else the tile-quarter-band median.

Valid pixels are never touched, and an input without nodata is returned as is (the same
object). Integer input stays integer: fills are rounded and clamped to the dtype, as in the
published run, whose stacks were int16.
"""

import torch
import torch.nn.functional as F

NODATA = -32768  # mosaic nodata DN; non-finite floats count as nodata too
NODATA_FILLS = ("window31",)
FILL_WINDOW = 31
FILL_MIN_VALID = 0.30


def bad_mask(x: torch.Tensor) -> torch.Tensor:
    bad = x == NODATA
    if x.is_floating_point():
        bad |= ~torch.isfinite(x)
    return bad


@torch.inference_mode()
def fill_nodata(
    x: torch.Tensor, window: int = FILL_WINDOW, min_valid: float = FILL_MIN_VALID
) -> torch.Tensor:
    """Fill nodata in a (16, H, W) stack, channel = quarter * 4 + band."""
    bad = bad_mask(x)
    has = bad.flatten(1).any(1).tolist()
    if not any(has):
        return x
    y = x.clone()
    for band in range(4):
        chans = [q * 4 + band for q in range(4)]
        if not any(has[c] for c in chans):
            continue
        valid = ~bad[chans]
        xb = torch.where(valid, x[chans].float(), 0.0)
        total = xb.sum(0)
        count = valid.sum(0, dtype=torch.int32)
        for q, c in enumerate(chans):
            if not has[c]:
                continue
            v = valid[q].float()[None, None]
            den = F.avg_pool2d(v, window, 1, window // 2)[0, 0]
            num = F.avg_pool2d(xb[q][None, None], window, 1, window // 2)[0, 0]
            others = count - valid[q].int()
            other_mean = (total - xb[q]) / others.clamp(min=1)
            use_window = bad[c] & (den >= min_valid)
            use_median = bad[c] & ~use_window & (others == 0)
            fill = torch.where(use_window, num / den.clamp(min=1e-6), other_mean)
            if bool(use_median.any()):
                vals = xb[q][valid[q]]
                med = vals.median() if vals.numel() else torch.zeros((), device=x.device)
                fill = torch.where(use_median, med, fill)
            if not x.is_floating_point():
                info = torch.iinfo(x.dtype)
                fill = fill.round().clamp(info.min + 1, info.max)
            y[c] = torch.where(bad[c], fill.to(x.dtype), x[c])
    return y
