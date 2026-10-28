"""Stream mosaic COGs from the mirror through the model and write the published score COGs.

Each tile's 16 band COGs (Q1-Q4 x B04, B03, B02, B08) are downloaded whole into memory, run
through ``predict.predict_tile`` and discarded. The scores are written with ``run.write_score``
at the bucket's own key, ``{out-root}/raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif``, so a
finished year uploads with ``tools/upload_data.py`` as is.

Downloads, the GPU and the COG writers overlap: ``--tile-workers`` tiles download at once, at
most ``--prefetch`` decoded tiles wait for the GPU, and ``--writers`` processes write COGs.
The writers are processes, not threads, because writer threads slowed the GPU loop in the same
process by up to 45%; each tile's scores reach its writer through shared memory.

The work list is ``tile_index_{year}.parquet`` (``pipeline/mosaics/indexes.py``) filtered by the
keep-list; a tile runs only with all four quarters. Every COG carries ``prov_fingerprint``
(``qa.provenance``), and an existing output with another fingerprint stops the shard unless
``--on-stale redo``. Per-tile statistics go into ``qa_*`` tags and ``{out-root}/_qa/{year}/``.
"""

import argparse
import hashlib
import json
import multiprocessing
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from multiprocessing import shared_memory
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
from affine import Affine
from rasterio.io import MemoryFile

# predict imports torch, whose CUDA libraries must load before ONNX Runtime's
from predict import BATCH, OVERLAP, PATCH, UP, predict_tile
from nodata import NODATA_FILLS
from qa import input_stats, output_stats, provenance, to_tags, write_sidecar
from run import INPUT_BANDS, ZSTD_LEVEL, cuda_options, model_contract, output_key, write_score
import onnxruntime as ort

USER_AGENT = "ftw-global-data-catalog (+https://github.com/fieldsoftheworld/ftw-global-data-catalog)"
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
MODEL_BANDS = ("b04", "b03", "b02", "b08")
RETRYABLE = {429, 500, 502, 503, 504}


def make_session(onnx_path: str, tf32: bool = False, cpu_threads: int = 2) -> ort.InferenceSession:
    """CUDA session with the pinned provider options; refuses a CPU fallback."""
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = cpu_threads  # ORT otherwise pins threads outside the cgroup
    so.inter_op_num_threads = 1
    opts = cuda_options(tf32)
    sess = ort.InferenceSession(onnx_path, so, providers=[("CUDAExecutionProvider", opts)])
    sess.disable_fallback()
    if sess.get_providers()[:1] != ["CUDAExecutionProvider"]:
        raise RuntimeError(f"CUDA execution provider not active: {sess.get_providers()}")
    got = sess.get_provider_options().get("CUDAExecutionProvider", {})
    for k, v in opts.items():
        if k in got and str(got[k]) != v:
            raise RuntimeError(f"CUDA provider {k}={got[k]!r}, requested {v!r}")
    return sess


def work_list(index: Path, keep: Path) -> list[tuple[str, list[str], list[int]]]:
    """(tile_key, 16 hrefs, 16 sizes) in model order for keep tiles with all four quarters."""
    keep_set = set(keep.read_text().split())
    by_tile: dict[str, dict[str, dict]] = {}
    for r in pq.read_table(index).to_pylist():
        if r["tile_key"] in keep_set:
            by_tile.setdefault(r["tile_key"], {})[r["quarter"]] = r
    out, partial = [], 0
    for tk, qs in sorted(by_tile.items()):
        if set(qs) != set(QUARTERS):
            partial += 1
            continue
        hrefs = [qs[q][f"{b}_href"] for q in QUARTERS for b in MODEL_BANDS]
        sizes = [int(qs[q][f"{b}_size"]) for q in QUARTERS for b in MODEL_BANDS]
        out.append((tk, hrefs, sizes))
    if partial:
        print(f"skipping {partial} keep tiles without all 4 quarters", flush=True)
    return out


def _download(url: str, expected: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
        etag = resp.headers.get("ETag", "")
    if len(data) != expected:  # a truncated body would decode as garbage
        raise OSError(f"short read {len(data)} != {expected}")
    return data, etag


def fetch_band(url: str, expected: int, attempts: int = 6) -> dict:
    """One whole-object GET and an in-memory decode (3x faster than /vsicurl range reads)."""
    for attempt in range(attempts):
        try:
            data, etag = _download(url, expected)
            with MemoryFile(data) as mf, mf.open() as ds:
                return {
                    "arr": ds.read(1),
                    "grid": (ds.transform, ds.crs, ds.shape, ds.dtypes[0], ds.nodata),
                    "etag": etag,
                }
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE or attempt == attempts - 1:
                raise
        except (OSError, urllib.error.URLError):
            if attempt == attempts - 1:
                raise
        time.sleep(random.uniform(0, min(60, 2**attempt)))
    raise RuntimeError("unreachable")


def check_grid(tk: str, grids: list[tuple]) -> None:
    """All 16 bands share transform, CRS, shape, dtype and nodata, or the stack misregisters."""
    first = grids[0]
    if first[3] != "int16" or first[4] != -32768:
        raise ValueError(f"{tk}: dtype/nodata {first[3]}/{first[4]}, expected int16/-32768")
    for i, g in enumerate(grids[1:], 1):
        if g != first:
            raise ValueError(f"{tk}: band {i} grid {g} differs from band 0 {first}")


def fetch_tile(job, band_pool: ThreadPoolExecutor) -> dict:
    tk, hrefs, sizes = job
    t0 = time.perf_counter()
    bands = list(band_pool.map(fetch_band, hrefs, sizes))
    check_grid(tk, [b["grid"] for b in bands])
    arr = np.stack([b["arr"] for b in bands])
    transform, crs = bands[0]["grid"][:2]
    in_stats = {
        **input_stats(arr),
        "etag_sha": hashlib.sha256("".join(b["etag"] for b in bands).encode()).hexdigest()[:16],
        "transform": ",".join(f"{v:.2f}" for v in tuple(transform)[:6]),
        "shape": f"{arr.shape[1]}x{arr.shape[2]}",
    }
    return {"tile_key": tk, "arr": arr, "transform": transform, "crs": crs,
            "in_stats": in_stats, "bytes": sum(sizes), "dl_s": time.perf_counter() - t0}


def source_items(year: int, tile_key: str) -> str:
    return ",".join(f"Sentinel-2_mosaic_{year}_{q}_{tile_key}" for q in QUARTERS)


def write_tile(dst: Path, qa_path: Path, out: np.ndarray, crs, transform: Affine, tags: dict,
               qa: dict, patch_rows: int) -> float:
    t0 = time.perf_counter()
    out_stats = output_stats(out, patch_rows)
    tags = {**tags, **to_tags("qa_in_", qa["in"]), **to_tags("qa_", out_stats)}
    write_sidecar(qa_path, {"tile_key": tags["tile_key"], "in": qa["in"], "out": out_stats,
                            "prov": qa["prov"]})
    write_score(dst, out, crs, transform, tags)
    return time.perf_counter() - t0


def to_shared(out: np.ndarray) -> shared_memory.SharedMemory:
    shm = shared_memory.SharedMemory(create=True, size=out.nbytes)
    np.ndarray(out.shape, out.dtype, buffer=shm.buf)[...] = out
    return shm


def release(fut, shm: shared_memory.SharedMemory) -> None:
    """Wait for a write, then free its shared memory whether or not it failed."""
    try:
        fut.result()
    finally:
        shm.close()
        shm.unlink()


def write_shared(name: str, shape: tuple[int, ...], *args) -> float:
    """``write_tile`` in a writer process, on scores in the shared memory block ``name``."""
    shm = shared_memory.SharedMemory(name=name, track=False)  # the parent unlinks it
    out = np.ndarray(shape, np.uint8, buffer=shm.buf)
    try:
        return write_tile(args[0], args[1], out, *args[2:])
    finally:
        del out
        shm.close()


def is_stale(dst: Path, fingerprint: str) -> bool:
    """An existing output written by another run (or without provenance)."""
    if not dst.exists():
        return False
    with rasterio.open(dst) as ds:
        return ds.tags().get("prov_fingerprint") != fingerprint


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--index-dir", type=Path, required=True)
    ap.add_argument("--keep-list", type=Path,
                    default=Path(__file__).resolve().parents[1] / "mosaics" / "keeplist.txt")
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--patch", type=int, default=PATCH,
                    help="model input side at 2.5 m, a multiple of 32 (patch / 4 px at 10 m)")
    ap.add_argument("--overlap", type=float, default=OVERLAP)
    ap.add_argument("--nodata-fill", choices=(*NODATA_FILLS, "none"), default=NODATA_FILLS[0])
    ap.add_argument("--tile-workers", type=int, default=3, help="tiles downloading at once")
    ap.add_argument("--band-workers", type=int, default=16, help="band GETs per tile")
    ap.add_argument("--prefetch", type=int, default=3, help="decoded tiles held in RAM")
    ap.add_argument("--writers", type=int, default=2, help="COG writer processes")
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_PROCID", 0)))
    ap.add_argument("--num-shards", type=int, default=int(os.environ.get("SLURM_NTASKS", 1)))
    ap.add_argument("--limit", type=int, default=0, help="stop after N tiles (testing)")
    ap.add_argument("--tiles-file", type=Path, help="only the tile keys in this file")
    ap.add_argument("--stats-file", type=Path, help="append one JSON line of timings per tile")
    ap.add_argument("--tf32", action="store_true",
                    help="TF32 convolutions: faster, but outputs no longer agree across GPUs")
    ap.add_argument("--no-cuda-sync", dest="cuda_sync", action="store_false",
                    help="drop the torch/ONNX Runtime stream barrier (reproduces a release defect)")
    ap.add_argument("--on-stale", choices=("error", "redo"), default="error",
                    help="an existing output of another run: stop the shard, or overwrite it")
    ap.add_argument("--require-gpu", help="refuse to start unless the GPU name contains this")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="run with uncommitted changes in pipeline/inference (never for a release)")
    a = ap.parse_args()
    if a.patch <= 0 or a.patch % 32 or not 0 <= a.overlap < 1:
        ap.error("--patch must be a positive multiple of 32 and --overlap in [0, 1)")
    fill = None if a.nodata_fill == "none" else a.nodata_fill

    jobs = work_list(a.index_dir / f"tile_index_{a.year}.parquet", a.keep_list)
    if a.tiles_file:
        only = set(a.tiles_file.read_text().split())
        jobs = [j for j in jobs if j[0] in only]
    jobs = jobs[a.shard :: a.num_shards]
    sess = make_session(a.onnx, a.tf32)
    if problem := model_contract(sess, a.patch):
        sys.exit(problem)
    prov = provenance(sess, a.onnx, nodata_fill=fill, batch=a.batch, patch=a.patch,
                      overlap=a.overlap, cuda_sync=a.cuda_sync, tf32=a.tf32)
    if not a.allow_dirty and (prov["code"] == "unknown" or prov["code"].endswith("-dirty")):
        sys.exit(f"code version {prov['code']!r}: commit first (or --allow-dirty for a test)")
    if a.require_gpu and a.require_gpu not in prov["gpu"]:
        sys.exit(f"GPU {prov['gpu']!r} does not match --require-gpu {a.require_gpu!r}")
    print(f"provenance: {json.dumps(prov)}", flush=True)

    def dst_of(tk: str) -> Path:
        return a.out_root / output_key(a.year, tk, "hive")

    stale = {j[0] for j in jobs if is_stale(dst_of(j[0]), prov["fingerprint"])}
    if stale and a.on_stale == "error":
        sys.exit(f"{len(stale)} existing outputs come from another run "
                 f"(e.g. {sorted(stale)[:5]}); use a fresh --out-root, or --on-stale redo")
    jobs = [j for j in jobs if j[0] in stale or not dst_of(j[0]).exists()]
    if a.limit:
        jobs = jobs[: a.limit]
    print(f"year {a.year} shard {a.shard}/{a.num_shards}: {len(jobs)} tiles to run", flush=True)
    if not jobs:
        return
    tags = {
        "model": Path(a.onnx).name,
        "year": str(a.year),
        "quantization": "uint8 = p*255",
        "zstd_level": str(ZSTD_LEVEL),
        "input_bands": INPUT_BANDS,
        "patch": str(a.patch),
        "overlap": str(a.overlap),
        "cuda_sync": "1" if a.cuda_sync else "0",
        "tf32": "1" if a.tf32 else "0",
        **({"nodata_fill": fill} if fill else {}),
        **to_tags("prov_", prov),
    }
    failed: list[str] = []
    band_pool = ThreadPoolExecutor(a.tile_workers * a.band_workers)
    tile_pool = ThreadPoolExecutor(a.tile_workers)
    writer = ProcessPoolExecutor(a.writers, mp_context=multiprocessing.get_context("spawn"))
    pending: deque = deque()
    todo = deque(jobs)
    inflight: set = set()
    done = gpu_s = idle_s = 0.0
    t_start = time.perf_counter()

    def finished(tk: str) -> bool:
        """Another job of this run wrote the tile; another run's output fails the shard."""
        dst = dst_of(tk)
        if tk in stale or not dst.exists():
            return False
        if not is_stale(dst, prov["fingerprint"]):
            return True
        if a.on_stale == "redo":
            return False
        failed.append(f"output of another run appeared mid-run: {dst}")
        return True

    def top_up() -> None:
        while todo and len(inflight) < a.prefetch:
            job = todo.popleft()
            if not finished(job[0]):
                inflight.add(tile_pool.submit(fetch_tile, job, band_pool))

    top_up()
    while inflight:
        t0 = time.perf_counter()
        ready, _ = wait(inflight, return_when=FIRST_COMPLETED)
        wait_s = time.perf_counter() - t0
        fut = ready.pop()
        inflight.discard(fut)
        try:
            tile = fut.result()
        except Exception as exc:  # the shard exits 1 at the end
            failed.append(f"{type(exc).__name__}: {exc}")
            print(f"  FETCH FAILED: {type(exc).__name__}: {exc}", flush=True)
            top_up()
            continue
        top_up()  # refill before computing so downloads overlap the GPU
        tk = tile["tile_key"]
        if finished(tk):
            continue
        t1 = time.perf_counter()
        stats: dict = {}
        out, npatch = predict_tile(sess, tile["arr"], batch=a.batch, patch=a.patch,
                                   overlap=a.overlap, nodata_fill=fill, stats=stats,
                                   cuda_sync=a.cuda_sync)
        compute_s = time.perf_counter() - t1
        del tile["arr"]
        while len(pending) > a.writers:  # each score array is ~3.2 GB
            release(*pending.popleft())
        shm = to_shared(out)
        shape = out.shape
        del out
        pending.append((writer.submit(
            write_shared, shm.name, shape, dst_of(tk),
            a.out_root / "_qa" / str(a.year) / f"{tk}.json",
            tile["crs"], tile["transform"] * Affine.scale(1.0 / UP),
            {**tags, "tile_key": tk, "source_items": source_items(a.year, tk)},
            {"in": {**tile["in_stats"], **stats}, "prov": prov}, a.patch,
        ), shm))
        if a.stats_file:
            with a.stats_file.open("a") as fh:
                fh.write(json.dumps({"tile_key": tk, "year": a.year, "dl_s": tile["dl_s"],
                                     "compute_s": compute_s, "patches": npatch,
                                     "host": os.uname().nodename, **stats}) + "\n")
        done += 1
        gpu_s += compute_s
        idle_s += wait_s
        print(f"[{int(done)}/{len(jobs)}] {tk}: dl {tile['bytes'] / 1e9:.2f} GB in "
              f"{tile['dl_s']:.0f}s | gpu idle {wait_s:.1f}s compute {compute_s:.1f}s "
              f"({npatch / compute_s:.0f} patches/s)", flush=True)
    while pending:
        release(*pending.popleft())
    writer.shutdown()
    wall = time.perf_counter() - t_start
    print(f"done {int(done)} tiles in {wall:.0f}s: {wall / max(done, 1):.1f}s/tile, "
          f"GPU busy {100 * gpu_s / wall:.0f}%, GPU waiting on data {idle_s:.0f}s", flush=True)
    if failed:
        sys.exit(f"{len(failed)} tiles failed (first: {failed[0]}); rerun the shard")


if __name__ == "__main__":
    main()
