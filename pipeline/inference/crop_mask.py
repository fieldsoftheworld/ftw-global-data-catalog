"""Zero scores farther than ``--buffer-m`` from io-lulc crop on the tiles in ``--tile-list``."""

import argparse
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError, WindowError
from rasterio.transform import array_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds
from scipy.ndimage import distance_transform_edt

from run import LAYOUTS, output_key, write_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mosaics"))
from cropland import IOLULC_CROPS_CLASS, IOLULC_YEARS, _bands, _is_absent, _iolulc_url  # noqa: E402

CELL = 16  # 40 m cells


def crop_grid(tile_key: str, crs, transform, shape: tuple[int, int]) -> np.ndarray:
    crop = np.zeros(shape, np.uint8)
    bounds = array_bounds(*shape, transform)
    _, south, _, north = transform_bounds(crs, "EPSG:4326", *bounds)
    for year in IOLULC_YEARS:
        for band, _, _ in _bands(south, north):
            url = _iolulc_url(f"{tile_key[:2]}{band}", year)
            try:
                src = rasterio.open(url)
            except RasterioIOError as exc:
                if _is_absent(exc):
                    continue
                raise
            with src:
                try:
                    win = from_bounds(*transform_bounds(crs, src.crs, *bounds), src.transform)
                    win = win.intersection(Window(0, 0, src.width, src.height))
                except WindowError:
                    continue
                part = np.zeros(shape, np.uint8)
                reproject(
                    (src.read(1, window=win) == IOLULC_CROPS_CLASS).astype(np.uint8),
                    part,
                    src_transform=src.window_transform(win),
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=crs,
                    resampling=Resampling.max,
                )
                crop |= part
    return crop.astype(bool)


def near_crop(crop: np.ndarray, cell_m: float, buffer_m: float) -> np.ndarray:
    if not crop.any():
        return crop
    return distance_transform_edt(~crop) * cell_m <= buffer_m


def apply_mask(scores: np.ndarray, keep: np.ndarray, cell: int = CELL) -> None:
    "Zero ``scores`` in place outside the coarse ``keep`` grid."
    for r in range(keep.shape[0]):
        drop = ~np.repeat(keep[r], cell)[: scores.shape[2]]
        scores[:, r * cell : (r + 1) * cell, drop] = 0


def mask_tile(src: Path, dst: Path, tile_key: str, buffer_m: float) -> float:
    "Returns the share of field pixels (score > 128) zeroed."
    with rasterio.open(src) as ds:
        scores, crs, transform, tags = ds.read(), ds.crs, ds.transform, ds.tags()
    shape = (-(-scores.shape[1] // CELL), -(-scores.shape[2] // CELL))
    coarse = transform * transform.scale(CELL)
    keep = near_crop(crop_grid(tile_key, crs, coarse, shape), abs(coarse.a), buffer_m)
    fields = int((scores[0] > 128).sum())
    apply_mask(scores, keep)
    dropped = fields - int((scores[0] > 128).sum())
    write_score(dst, scores, crs, transform, {**tags, "crop_mask_buffer_m": str(buffer_m)})
    return dropped / fields if fields else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--tile-list", type=Path, required=True)
    ap.add_argument("--buffer-m", type=float, required=True)
    ap.add_argument("--layout", choices=tuple(LAYOUTS), default="hive")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    a = ap.parse_args()
    if a.input_dir.resolve() == a.output_dir.resolve():
        ap.error("input and output directories must differ")
    tiles = sorted(a.tile_list.read_text().split())[a.shard :: a.num_shards]
    for tile in tiles:
        key = output_key(a.year, tile, a.layout)
        src, dst = a.input_dir / key, a.output_dir / key
        if dst.exists():
            with rasterio.open(src) as s, rasterio.open(dst) as d:
                st, dt = s.tags(), d.tags()
            same_fp = dt.get("inference_fingerprint") == st.get("inference_fingerprint")
            if same_fp and dt.get("crop_mask_buffer_m") == str(a.buffer_m):
                continue
        share = mask_tile(src, dst, tile, a.buffer_m)
        print(f"{tile}: {share:.1%} of field pixels zeroed", flush=True)


if __name__ == "__main__":
    main()
