"""Golden crops and the comparison metrics ``selftest.py`` gates on.

Inputs and references live outside git, under ``--root`` or ``FTW_GOLDEN``.
"""

from dataclasses import dataclass

import numpy as np

from predict import NATIVE, OVERLAP, UP

STRIDE = round(NATIVE * (1 - OVERLAP))
MOSAICS = "https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics"


@dataclass(frozen=True)
class Crop:
    name: str
    tile_key: str
    year: int
    row: int
    col: int
    height: int = NATIVE + 2 * STRIDE  # sizes on the full tile's patch grid
    width: int = NATIVE + 6 * STRIDE
    why: str = ""


CROPS = (
    Crop("top_left", "48PWR_0_0", 2025, 0, 0, why="tile corner, first batch, 16-row mosaic seam"),
    Crop("nodata_2017", "20HQC_0_0", 2017, 2880, 0, why="2017 Q2 ~60% nodata, left tile edge"),
    Crop("monsoon_edge", "43PER_0_0", 2025, 10008 - (NATIVE + 2 * STRIDE),
         10008 - (NATIVE + 6 * STRIDE), why="bottom-right corner (clamped last patches)"),
    Crop("interior", "15TVG_0_0", 2021, 4800, 4800, why="dense Iowa fields, interior"),
)


def band_hrefs(c: Crop) -> list[str]:
    """The 16 band COG URLs in model order (Q1-Q4 x R, G, B, N)."""
    return [
        f"{MOSAICS}/{c.year}/{q}/{c.tile_key}/{b}.tif"
        for q in ("Q1", "Q2", "Q3", "Q4")
        for b in ("B04", "B03", "B02", "B08")
    ]


def diff_stats(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """Per-band max and mean |a - b| of (2, H, W) uint8 scores, and the share off by >1 and >4."""
    if a.shape != b.shape:
        raise ValueError(f"shape {a.shape} != {b.shape}")
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    out: dict[str, float] = {}
    for i, name in enumerate(("field", "boundary")):
        out[f"{name}_max"] = float(d[i].max())
        out[f"{name}_mean"] = float(d[i].mean())
        out[f"{name}_gt1"] = float((d[i] > 1).mean())
        out[f"{name}_gt4"] = float((d[i] > 4).mean())
    return out


def border_seam_ratio(out: np.ndarray, start: int = 0) -> float:
    """Mean step across patch borders over the mean step elsewhere, field band, worst axis.

    A blending or placement bug shows as a ratio well above 1.
    """
    f = out[0].astype(np.float32)
    ratios = []
    for prof in (np.abs(np.diff(f, axis=0)).mean(1), np.abs(np.diff(f, axis=1)).mean(0)):
        n = prof.size
        idx = {e - 1 for k in range(start, n + 1, STRIDE * UP)
               for e in (k, k + NATIVE * UP) if 0 < e < n}
        border = np.array(sorted(idx))
        other = np.setdiff1d(np.arange(n), border)
        ratios.append(float(prof[border].mean() / max(prof[other].mean(), 1e-6)))
    return max(ratios)


def strip_means(out: np.ndarray, rows: int = NATIVE * UP) -> dict[str, float]:
    """Mean field and boundary in the top patch row and the patch row below it."""
    f, b = out[0], out[1]
    return {
        "top_field": float(f[:rows].mean() / 255),
        "next_field": float(f[rows : 2 * rows].mean() / 255),
        "top_boundary": float(b[:rows].mean() / 255),
        "next_boundary": float(b[rows : 2 * rows].mean() / 255),
    }
