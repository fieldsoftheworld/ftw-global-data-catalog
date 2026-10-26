"""Checks of one score COG as ``run.write_score`` writes it.

Two uint8 bands (field, boundary), 40,032 px at 2.5 m, no nodata, ZSTD with predictor 2, 512 px
blocks, pixel interleave, and overviews 4-64 built with ``average`` resampling. GDAL builds each
overview from the level above (the first from the base) as the area-weighted mean of the source
pixels it covers, rounded half up; ``overview_chain`` recomputes exactly that, so a correct file
differs by 0 counts.
"""

import math
from collections.abc import Iterator

import numpy as np
import rasterio
from rasterio.windows import Window

from run import OVERVIEWS

BLOCK = 512
SIZE = 40032  # 4 x the 10,008 px mosaic tile
STAT_KEYS = (
    "STATISTICS_MINIMUM",
    "STATISTICS_MAXIMUM",
    "STATISTICS_MEAN",
    "STATISTICS_STDDEV",
    "STATISTICS_VALID_PERCENT",
)


def expected_epsg(tile_key: str) -> int:
    """UTM EPSG of an MGRS tile key: latitude bands C-M are south, N-X north."""
    zone, band = int(tile_key[:2]), tile_key[2].upper()
    return (32700 if band < "N" else 32600) + zone


def level_shape(factor: int, size: int = SIZE) -> int:
    """GDAL's overview size for a decimation factor."""
    return -(-size // factor)


def _tag(ds: rasterio.DatasetReader, name: str, ovr: int | None = None) -> int | None:
    v = ds.get_tag_item(name, "TIFF", bidx=1, ovr=ovr)
    return int(v) if v not in (None, "") else None


def layout_errors(ds: rasterio.DatasetReader, file_size: int) -> list[str]:
    """The COG layout GDAL's validator checks, and the base's last block inside the file.

    Every IFD comes before the image data, IFDs run base to smallest overview, and image data runs
    the other way.
    """
    err = []
    ims = ds.tags(ns="IMAGE_STRUCTURE")
    if ims.get("LAYOUT") != "COG":
        err.append(f"layout {ims.get('LAYOUT')!r} != COG")
    if ims.get("COMPRESSION") != "ZSTD":
        err.append(f"compression {ims.get('COMPRESSION')!r}")
    if ims.get("INTERLEAVE") != "PIXEL":
        err.append(f"interleave {ims.get('INTERLEAVE')!r}")
    levels: list[int | None] = [None, *range(len(ds.overviews(1)))]
    ifd = [_tag(ds, "IFD_OFFSET", o) for o in levels]
    data = [_tag(ds, "BLOCK_OFFSET_0_0", o) for o in levels]
    if None in ifd or None in data:
        return [*err, f"missing IFD/block offsets: ifd={ifd} data={data}"]
    if ifd != sorted(ifd):
        err.append(f"IFDs out of order {ifd}")
    if max(ifd) > min(data):
        err.append(f"IFD after image data (ifd {max(ifd)} > data {min(data)})")
    if data != sorted(data, reverse=True):
        err.append(f"image data not smallest-overview-first {data}")
    nb = -(-ds.width // BLOCK) - 1
    off, cnt = _tag(ds, f"BLOCK_OFFSET_{nb}_{nb}"), _tag(ds, f"BLOCK_SIZE_{nb}_{nb}")
    if off is None or cnt is None or off + cnt > file_size:
        err.append(f"truncated: last base block {off}+{cnt} > file size {file_size}")
    return err


def level_errors(path: str, n: int = len(OVERVIEWS)) -> list[str]:
    """Each overview level is ceil(W/f) x ceil(H/f), two uint8 bands, in 512 px blocks."""
    err = []
    with rasterio.open(path) as ds:
        if len(ds.overviews(1)) != n or len(ds.overviews(2)) != n:
            err.append(f"overview levels {ds.overviews(1)} / {ds.overviews(2)}, want {n}")
        if ds.block_shapes != [(BLOCK, BLOCK)] * 2:
            err.append(f"base blocks {ds.block_shapes}")
        w, h = ds.width, ds.height
    for i, f in enumerate(OVERVIEWS[:n]):
        try:
            with rasterio.open(path, overview_level=i) as o:
                want = (level_shape(f, w), level_shape(f, h))
                if (o.width, o.height) != want or o.count != 2 or set(o.dtypes) != {"uint8"}:
                    err.append(f"level {f}: {o.width}x{o.height}x{o.count} {o.dtypes} != {want}")
                if o.block_shapes != [(BLOCK, BLOCK)] * 2:
                    err.append(f"level {f}: blocks {o.block_shapes}")
        except Exception as e:
            err.append(f"level {f}: {type(e).__name__}: {str(e)[:80]}")
    return err


def area_weights(o0: int, o1: int, ratio: float) -> tuple[np.ndarray, int]:
    """Row-normalized weights (output pixels o0..o1-1 x sources) and the first source pixel."""
    s0, s1 = math.floor(o0 * ratio), math.ceil(o1 * ratio)
    w = np.zeros((o1 - o0, s1 - s0))
    for j in range(o0, o1):
        a, b = j * ratio, (j + 1) * ratio
        for s in range(math.floor(a), math.ceil(b)):
            w[j - o0, s - s0] = min(b, s + 1) - max(a, s)
    return w / w.sum(axis=1, keepdims=True), s0


def _read(path: str, level: int | None, win: Window) -> np.ndarray:
    kw = {"overview_level": level} if level is not None else {"OVERVIEW_LEVEL": "NONE"}
    with rasterio.open(path, **kw) as d:
        return d.read(window=win)


def overview_chain(path: str, windows: list[tuple[int, int, int]]) -> tuple[int, list[str]]:
    """Recompute every overview level over ``windows`` (base x0, y0, size) from the level above.

    Returns the largest difference in counts and an error for each window off by more than 1.
    """
    worst, err = 0, []
    with rasterio.open(path) as ds:
        sizes = [(ds.width, ds.height)]
    for f in OVERVIEWS:
        sizes.append((level_shape(f, sizes[0][0]), level_shape(f, sizes[0][1])))
    for x0, y0, n in windows:
        for i, f in enumerate(OVERVIEWS):
            (pw, ph), (w, h) = sizes[i], sizes[i + 1]
            ox0, oy0 = x0 // f, y0 // f
            ox1, oy1 = min(w, -(-(x0 + n) // f)), min(h, -(-(y0 + n) // f))
            wx, sx0 = area_weights(ox0, ox1, pw / w)
            wy, sy0 = area_weights(oy0, oy1, ph / h)
            src = _read(path, i - 1 if i else None, Window(sx0, sy0, wx.shape[1], wy.shape[1]))
            if src.shape[1:] != (wy.shape[1], wx.shape[1]):
                err.append(f"level {f} window {x0},{y0}: source read {src.shape}")
                continue
            ref = np.floor(wy @ src.astype(np.float64) @ wx.T + 0.5)
            got = _read(path, i, Window(ox0, oy0, ox1 - ox0, oy1 - oy0)).astype(np.float64)
            d = int(np.abs(got - ref).max()) if got.shape == ref.shape else 255
            worst = max(worst, d)
            if d > 1:
                err.append(f"level {f} window {x0},{y0}: max diff {d}")
    return worst, err


def pick_windows(path: str, k: int = 3, n: int = 2048, seed: int = 0) -> list[tuple[int, int, int]]:
    """``k`` base windows with the most field signal in the coarsest level, plus one at random."""
    with rasterio.open(path) as ds:
        size = min(ds.width, ds.height)
    with rasterio.open(path, overview_level=len(OVERVIEWS) - 1) as o:
        f = o.read(1).astype(np.float64)
        fac = size / min(o.width, o.height)
    n = min(n, size // 64 * 64)
    grid = []
    for y0 in range(0, size - n + 1, n):
        for x0 in range(0, size - n + 1, n):
            y, x, m = int(y0 / fac), int(x0 / fac), max(1, int(n / fac))
            grid.append((float(f[y : y + m, x : x + m].mean()), x0, y0))
    grid.sort(key=lambda g: -g[0])
    ri = int(np.random.default_rng(seed).integers(len(grid)))
    return [(x0, y0, n) for _, x0, y0 in [*grid[:k], grid[ri]]]


def longest_run(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return int((np.flatnonzero(d == -1) - np.flatnonzero(d == 1)).max())


def level_stats(path: str) -> list[dict[str, float]]:
    """Per overview level: field and boundary mean, nonzero share, longest all-zero row run."""
    out = []
    for i, f in enumerate(OVERVIEWS):
        with rasterio.open(path, overview_level=i) as o:
            a = o.read()
        out.append({
            "factor": f,
            "field_mean": float(a[0].mean(dtype=np.float64) / 255),
            "boundary_mean": float(a[1].mean(dtype=np.float64) / 255),
            "nonzero": float((a.max(axis=0) > 0).mean()),
            "zero_row_run": longest_run(a.max(axis=(0, 2)) == 0) * f,
        })
    return out


def base_chunks(ds: rasterio.DatasetReader, rows: int = 4096) -> Iterator[np.ndarray]:
    for r in range(0, ds.height, rows):
        yield ds.read(window=Window(0, r, ds.width, min(rows, ds.height - r)))


def base_means(path: str) -> dict[str, float]:
    """Field and boundary mean of the full-resolution base, every block decoded."""
    sums = np.zeros(2)
    with rasterio.open(path, OVERVIEW_LEVEL="NONE") as ds:
        npx = ds.width * ds.height
        for a in base_chunks(ds):
            sums += a.sum(axis=(1, 2), dtype=np.float64)
    return {"field_mean": float(sums[0] / npx / 255), "boundary_mean": float(sums[1] / npx / 255)}


def embedded_stats(path: str) -> list[dict[str, str]]:
    """Each band's STATISTICS_* tags as stored in the file itself (PAM off)."""
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(path) as ds:
        return [
            {k: v for k, v in ds.tags(b).items() if k.startswith("STATISTICS_")}
            for b in range(1, ds.count + 1)
        ]
