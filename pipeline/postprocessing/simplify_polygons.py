"Simplify per-tile GeoParquet in UTM and refresh bounds."

import argparse
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

from pool_utils import drain, exit_on_failures

from polygons import simplify_coverage

#: See ``outlines.MP_CONTEXT``.
MP_CONTEXT = multiprocessing.get_context("spawn")

IN = Path("outlines")
OUT = Path("simplified")


def utm_epsg(tile_key: str) -> int:
    return int(f"32{6 if tile_key[2] >= 'N' else 7}{tile_key[:2]}")


FP_KEY = b"simplify_fingerprint"


def fingerprint(src: Path, tol: float) -> bytes:
    st = src.stat()
    return f"tol={tol};size={st.st_size};mtime_ns={st.st_mtime_ns}".encode()


def is_current(dst: Path, src: Path, tol: float) -> bool:
    "True when dst is a readable parquet made from this exact source at this tolerance."
    if not dst.exists():
        return False
    try:
        md = pq.ParquetFile(dst).metadata
        kv = md.metadata or {}
        return kv.get(FP_KEY) == fingerprint(src, tol) and md.num_rows >= 0
    except (OSError, pa.ArrowException):
        return False


def write_atomic(tbl: pa.Table, dst: Path, fp: bytes) -> None:
    tbl = tbl.replace_schema_metadata({**(tbl.schema.metadata or {}), FP_KEY: fp})
    tmp = dst.with_name(dst.name + f".tmp-{os.getpid()}")
    pq.write_table(tbl, tmp, compression="zstd")
    os.replace(tmp, dst)


def process_tile(tk: str, year: int, tol: float, in_root: Path, out_root: Path) -> tuple:
    t = time.perf_counter()
    src = in_root / str(year) / f"{tk}.parquet"
    dst = out_root / str(year) / f"{tk}.parquet"
    if is_current(dst, src, tol):
        return tk, -1, 0, 0, 0.0
    fp = fingerprint(src, tol)
    tbl = pq.read_table(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if tbl.num_rows == 0:
        write_atomic(tbl, dst, fp)
        return tk, 0, 0, 0, time.perf_counter() - t
    g = shapely.from_wkb(tbl["geometry"].to_numpy(zero_copy_only=False))
    fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg(tk)}", always_xy=True)
    proj = shapely.transform(g, lambda a: np.c_[fwd.transform(a[:, 0], a[:, 1])])
    n0 = int(shapely.get_num_coordinates(proj).sum())
    simp = simplify_coverage(proj, tol, label=tk)
    n1 = int(shapely.get_num_coordinates(simp).sum())
    back = shapely.transform(
        simp, lambda a: np.c_[fwd.transform(a[:, 0], a[:, 1], direction="INVERSE")]
    )
    b = shapely.bounds(back)
    cols = {k: tbl[k] for k in tbl.column_names}
    for j, k in enumerate(("xmin", "ymin", "xmax", "ymax")):
        cols[k] = pa.array(b[:, j])
    cols["geometry"] = pa.array(shapely.to_wkb(back))
    write_atomic(pa.table(cols).replace_schema_metadata(tbl.schema.metadata), dst, fp)
    return tk, tbl.num_rows, n0, n1, time.perf_counter() - t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--tolerance-m", type=float, default=5.0)
    ap.add_argument("--in-root", type=Path, default=IN)
    ap.add_argument("--out-root", type=Path, default=OUT)
    ap.add_argument("--tiles", nargs="*")
    ap.add_argument("--tile-list", type=Path, help="one tile key per line (size-class lists)")
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument(
        "--num-shards", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_COUNT", 1))
    )
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.in_root.resolve() == a.out_root.resolve():
        ap.error("input and output roots must differ")
    if a.workers < 1 or a.num_shards < 1 or not 0 <= a.shard < a.num_shards:
        ap.error("invalid workers or shard")
    tiles = a.tiles or (
        a.tile_list.read_text().split()
        if a.tile_list
        else sorted(p.stem for p in (a.in_root / str(a.year)).glob("*.parquet"))
    )
    tiles = tiles[a.shard :: a.num_shards]
    tiles = [
        t
        for t in tiles
        if not is_current(
            a.out_root / str(a.year) / f"{t}.parquet",
            a.in_root / str(a.year) / f"{t}.parquet",
            a.tolerance_m,
        )
    ]
    print(f"{len(tiles)} tiles, shard {a.shard}/{a.num_shards}, tol {a.tolerance_m} m", flush=True)
    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1, mp_context=MP_CONTEXT) as pool:
        futs = {
            pool.submit(process_tile, t, a.year, a.tolerance_m, a.in_root, a.out_root): t
            for t in tiles
        }

        def report(tk: str, res: tuple) -> None:
            _, n, c0, c1, dt = res
            if n >= 0:
                print(f"  {tk}: {n:,} parcels, coords {c0:,} -> {c1:,}, {dt:.0f}s", flush=True)

        failures = drain(futs, report)
    exit_on_failures(failures, len(tiles), "tiles")


if __name__ == "__main__":
    main()
