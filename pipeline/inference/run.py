"""Run an FP32 ONNX model over stacked quarterly mosaic tiles.

Outputs land directly at the bucket's own grouped key:
``{output-dir}/raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif``, so a finished
year uploads with ``tools/upload_data.py`` to where the catalog's items point,
with no relayout step (point ``--output-dir`` at the repo's ``staging-data/``).
``--layout item`` writes the older per-item-folder key
``raster/{year}/{tile}/{tile}.tif``, which no catalog references any more.
"""

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import torch  # imported before ONNX Runtime so its CUDA libraries load first
import onnxruntime as ort
import rasterio
from affine import Affine
import rasterio.shutil
from rasterio.enums import Resampling
from rasterio.windows import Window
from nodata import NODATA_FILLS
from predict import PATCH, predict_tile

PROVIDERS = {"cuda": "CUDAExecutionProvider", "cpu": "CPUExecutionProvider"}
#: CUDA execution-provider options the published 2026-10 run pinned. ONNX Runtime's default is
#: TF32 on, which makes the GPU output depend on batch size and on the cuDNN algorithm the timing
#: search picks (up to 232/255 where nodata reaches the model); with it off, H100, A100 and CPU
#: FP32 agree within 1/255. ``--tf32`` is the ~45% faster, less reproducible alternative.
CUDA_OPTIONS = {"cudnn_conv_algo_search": "EXHAUSTIVE", "use_tf32": "0"}
# The contract the mosaic pipeline writes (pipeline/mosaics/download.py): quarter-major.
BANDS = ("B04", "B03", "B02", "B08")
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
BAND_DESCRIPTIONS = tuple(f"{q}_{b}" for q in QUARTERS for b in BANDS)
INPUT_BANDS = f"{','.join(QUARTERS)} x {','.join(BANDS)}"
#: Overview factors of the released COGs: 10-160 m. There is no 5 m level, which would add
#: ~40% to the file size for a resolution the 10 m inputs do not have.
OVERVIEWS = (4, 8, 16, 32, 64)
ZSTD_LEVEL = 9


def read_stack(path: Path):
    with rasterio.open(path) as ds:
        if ds.count != 16 or ds.crs is None:
            raise ValueError(f"{path}: expected 16 georeferenced bands")
        if not np.isclose(ds.transform.a, 10) or not np.isclose(ds.transform.e, -10):
            raise ValueError(f"{path}: expected north-up 10 m pixels")
        if not np.isclose(ds.transform.b, 0) or not np.isclose(ds.transform.d, 0):
            raise ValueError(f"{path}: rotated input grid")
        tags = ds.tags()
        described, declared = tuple(ds.descriptions), tags.get("input_bands")
        if any(described) and described != BAND_DESCRIPTIONS:
            raise ValueError(f"{path}: band descriptions {described} are not {BAND_DESCRIPTIONS}")
        if declared is not None and declared != INPUT_BANDS:
            raise ValueError(f"{path}: input_bands tag {declared!r} is not {INPUT_BANDS!r}")
        if described != BAND_DESCRIPTIONS and declared != INPUT_BANDS:
            raise ValueError(
                f"{path}: band order unconfirmed — needs {BAND_DESCRIPTIONS} band descriptions "
                f"or an input_bands={INPUT_BANDS!r} tag"
            )
        arr = ds.read()
        if arr.dtype not in (np.int16, np.float32):
            arr = arr.astype(np.float32)
        # int16 mosaics stay int16: the window31 fill rounds to the stack's own dtype, as in the
        # published run, and the host copy is half the size
        return arr, ds.transform, ds.crs, tags


def cuda_options(tf32: bool = False) -> dict:
    """The CUDA provider options of a run: ``CUDA_OPTIONS``, with TF32 only when asked for."""
    return {**CUDA_OPTIONS, "use_tf32": "1" if tf32 else "0"}


def make_session(model: Path, device: str, tf32: bool = False):
    """ONNX Runtime session on ``device``; CUDA gets the pinned options of ``cuda_options``."""
    provider = PROVIDERS[device]
    spec = (provider, cuda_options(tf32)) if device == "cuda" else provider
    return ort.InferenceSession(str(model), providers=[spec])


def options_applied(session, device: str, tf32: bool = False) -> str | None:
    """Error message if the CUDA provider did not take the requested options (CPU: always None)."""
    if device != "cuda":
        return None
    got = session.get_provider_options().get(PROVIDERS["cuda"], {})
    for key, want in cuda_options(tf32).items():
        if key in got and str(got[key]) != want:
            return f"CUDA provider option {key}={got[key]!r}, requested {want!r}"
    return None


def fingerprint(
    src: Path,
    model_hash: str,
    batch: int,
    overlap: float,
    norm: float,
    provider: str,
    nodata_fill: str | None = None,
    tf32: bool = False,
    cuda_sync: bool = True,
) -> str:
    """Resume identity: the input file, the model and every setting that changes the output."""
    st = src.stat()
    return json.dumps(
        [
            st.st_size, st.st_mtime_ns, model_hash, batch, overlap, norm, provider,
            nodata_fill, tf32, cuda_sync,
        ]
    )


#: Object keys under the publish prefix, relative to ``--output-dir``. ``item`` is what
#: the released 2e tiles were uploaded from; ``hive`` is the key the bucket itself uses
#: (``tools/build_raster_items.py``'s ``GROUPED_PATH``), so a hive run needs no
#: server-side regrouping. The zone/GZD slices are that module's ``zone_of``/``gzd_of``:
#: every one of the 67,197 tile keys matches ``\d{2}[A-Z]{3}_\d+_\d+``.
LAYOUTS = {
    "item": "raster/{year}/{tile}/{tile}.tif",
    "hive": "raster/{year}/zone={zone}/gzd={gzd}/{tile}/{tile}.tif",
}


def output_key(year: int, tile_key: str, layout: str) -> str:
    """Where a tile's COG lives under ``--output-dir``, for one of ``LAYOUTS``."""
    return LAYOUTS[layout].format(
        year=year, tile=tile_key, zone=tile_key[:2], gzd=tile_key[:3]
    )


def output_path(output_dir: Path, src: Path, year: int, layout: str) -> Path:
    """The COG path for one input stack. ``--year`` is the only year authority.

    The stack's own ``year`` tag is not consulted here: the processing loop's
    fail-closed guard rejects a tag that disagrees with ``--year`` and supplies
    ``--year`` when the tag is absent, so a second source would only let the
    path and the guard drift apart.
    """
    return output_dir / output_key(year, src.stem, layout)


def output_tags(
    src_tags: dict,
    model_hash: str,
    fp: str,
    norm: float,
    overlap: float,
    provider: str,
    model_name: str | None = None,
    tile_key: str | None = None,
    nodata_fill: str | None = None,
    tf32: bool = False,
    cuda_sync: bool = True,
) -> dict:
    """Source tags plus this run's provenance; the input's verified band-order tag is kept.

    ``model``, ``quantization``, ``zstd_level`` and ``tile_key`` are the tags the released
    COGs carry (``model`` and ``tile_key`` are written only when given). ``nodata_fill`` (only
    when a fill ran), ``cuda_sync`` and ``tf32`` record the numerics the run chose.
    """
    tags = dict(
        src_tags,
        quantization="uint8 = p*255",
        zstd_level=str(ZSTD_LEVEL),
        model_sha256=model_hash,
        inference_fingerprint=fp,
        execution_provider=provider,
        normalization=str(norm),
        overlap=str(overlap),
        cuda_sync="1" if cuda_sync else "0",
        tf32="1" if tf32 else "0",
    )
    if nodata_fill:
        tags["nodata_fill"] = nodata_fill
    tags.setdefault("input_bands", INPUT_BANDS)
    if model_name:
        tags["model"] = model_name
    if tile_key:
        tags["tile_key"] = tile_key
    return tags


def current(dst: Path, fp: str) -> bool:
    if not dst.exists():
        return False
    try:
        with rasterio.open(dst) as ds:
            return ds.count == 2 and ds.tags().get("inference_fingerprint") == fp
    except rasterio.errors.RasterioIOError:
        return False


def device_available(device: str) -> str | None:
    """Error message if `device` is not actually usable on this host.

    `ort.get_available_providers()` lists what the wheel was compiled with, not what
    the machine can run: onnxruntime-gpu advertises CUDA on any Linux host, driver or
    not. Only torch can answer whether a device is really there, and predict_tile's
    `.to("cuda")` is what would otherwise discover it, one full tile read too late.
    """
    provider = PROVIDERS[device]
    if provider not in ort.get_available_providers():
        return f"{provider} is missing from this onnxruntime build"
    if device == "cuda" and not torch.cuda.is_available():
        return "--device cuda, but torch sees no CUDA device (check the driver and the GPU)"
    return None


def active_provider(session, provider: str) -> str | None:
    """Error message if ONNX Runtime quietly fell back off `provider` loading the model."""
    if provider not in session.get_providers():
        return f"{provider} did not initialize; the session runs on {session.get_providers()}"
    return None


def model_contract(session) -> str | None:
    """Error message if the session is not dynamic-batch [B,16,P,P] -> [B,3,P,P] FP32."""
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].name != "input" or inputs[0].type != "tensor(float)":
        return "model must take a single FP32 'input'"
    if not outputs or outputs[0].name != "logits" or outputs[0].type != "tensor(float)":
        return "model must emit FP32 'logits'"
    for io, channels in ((inputs[0], 16), (outputs[0], 3)):
        if list(io.shape[1:]) != [channels, PATCH, PATCH]:
            return f"expected {io.name} [B,{channels},{PATCH},{PATCH}], got {io.shape}"
        if isinstance(io.shape[0], int):
            return (
                f"{io.name} has a fixed batch of {io.shape[0]}; re-export with a dynamic "
                "batch axis (the last batch of a tile is almost never full)"
            )
    return None


def same_pixels(path: Path, scores: np.ndarray, rows: int = 4096) -> None:
    """Raise unless the file at ``path`` holds exactly ``scores`` at full resolution.

    Guards against a COG whose base level was lost while its overviews survived:
    44 published 2019-2021 tiles had an empty base under valid overviews,
    which no header check or overview read catches.
    """
    with rasterio.open(path) as ds:
        if (ds.count, ds.height, ds.width) != scores.shape:
            raise ValueError(
                f"{path.name}: shape {ds.count}x{ds.height}x{ds.width} != {scores.shape}"
            )
        for r in range(0, ds.height, rows):
            w = Window(0, r, ds.width, min(rows, ds.height - r))
            if not np.array_equal(ds.read(window=w), scores[:, r : r + w.height]):
                raise ValueError(
                    f"{path.name}: full-resolution pixels differ from scores at row {r}"
                )


def write_score(dst: Path, scores: np.ndarray, crs, transform, tags: dict) -> None:
    """Write the two-band uint8 scores as a COG with overviews 4-64, verified before replace.

    Two steps, as the released tiles were built: a tiled GTiff gets the overviews, then the COG
    driver reuses them (``OVERVIEWS=FORCE_USE_EXISTING``). The COG driver alone would build
    levels 2-128, including a 5 m level, and cannot be told otherwise.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    # A SIGKILLed task (a Slurm timeout or OOM, the usual failure here) never runs the
    # `finally` below, so a full-size leftover outlives it and resume ignores it. Shards
    # are disjoint by tile, so nothing else is mid-write on this name.
    for orphan in (*dst.parent.glob(f"{dst.name}.stage-*"), *dst.parent.glob(f"{dst.name}.tmp-*")):
        orphan.unlink(missing_ok=True)
    stage = dst.with_name(f"{dst.name}.stage-{os.getpid()}")
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}")
    try:
        with rasterio.open(
            stage,
            "w",
            driver="GTiff",
            height=scores.shape[1],
            width=scores.shape[2],
            count=2,
            dtype="uint8",
            crs=crs,
            transform=transform,
            tiled=True,
            blockxsize=512,
            blockysize=512,
            compress="ZSTD",
            zstd_level=ZSTD_LEVEL,
            predictor=2,
            BIGTIFF="IF_SAFER",
        ) as ds:
            ds.write(scores)
            ds.scales = (1 / 255, 1 / 255)
            ds.offsets = (0.0, 0.0)
            ds.set_band_description(1, "field")
            ds.set_band_description(2, "boundary")
            ds.update_tags(**tags)
            # Embedded band statistics are a Portolan MUST (PTL-DAT-009,
            # read with PAM disabled, so a sidecar does not count). The
            # array is already in memory, so exact statistics are free here
            # — unlike retrofitting them into a published COG, which would
            # rewrite the file. They are written on the staged GTiff because
            # per-band GDAL_METADATA survives the COG copy below.
            for bidx in range(scores.shape[0]):
                band = scores[bidx]
                ds.update_tags(
                    bidx + 1,
                    STATISTICS_MINIMUM=int(band.min()),
                    STATISTICS_MAXIMUM=int(band.max()),
                    STATISTICS_MEAN=float(band.mean()),
                    STATISTICS_STDDEV=float(band.std()),
                    STATISTICS_VALID_PERCENT=100.0,
                )
        with rasterio.open(stage, "r+") as ds:
            ds.build_overviews(list(OVERVIEWS), Resampling.average)
        rasterio.shutil.copy(
            stage,
            tmp,
            driver="COG",
            COMPRESS="ZSTD",
            LEVEL=str(ZSTD_LEVEL),
            PREDICTOR="2",
            BLOCKSIZE="512",
            BIGTIFF="IF_SAFER",
            OVERVIEWS="FORCE_USE_EXISTING",
        )
        # The stage is as large as the COG, so dropping it here halves peak
        # scratch per in-flight tile; the `finally` stays as the error path.
        stage.unlink(missing_ok=True)
        same_pixels(tmp, scores)
        os.replace(tmp, dst)
    finally:
        stage.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True,
                    help="hierarchy root; scores land at "
                         "{output-dir}/" + LAYOUTS["hive"] + " (see --layout)")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--overlap", type=float, default=0.25)
    ap.add_argument("--norm", type=float, default=3000)
    ap.add_argument("--device", choices=tuple(PROVIDERS), default="cuda")
    ap.add_argument(
        "--nodata-fill",
        choices=(*NODATA_FILLS, "none"),
        default=NODATA_FILLS[0],
        help="fill mosaic nodata before inference (default window31, what the 2026-10 run used; "
        "none passes the raw -32768 to the model, which reproduces the earlier release)",
    )
    ap.add_argument(
        "--tf32",
        action="store_true",
        help="allow TF32 convolutions on CUDA (~45%% faster; output no longer agrees across "
        "GPUs and batch sizes within 1/255)",
    )
    ap.add_argument(
        "--no-cuda-sync",
        dest="cuda_sync",
        action="store_false",
        help="drop the torch / ONNX Runtime stream barrier (reproduces the earlier release)",
    )
    ap.add_argument(
        "--layout",
        choices=tuple(LAYOUTS),
        default="hive",
        help="hive (default): the bucket's own grouped key " + LAYOUTS["hive"]
        + ", which the catalog references and uploads with no regrouping; "
        "item: " + LAYOUTS["item"] + ", the superseded per-item-folder layout "
        "(no catalog points at it — use only to reproduce older runs)",
    )
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    a = ap.parse_args()
    if a.input_dir.resolve() == a.output_dir.resolve():
        ap.error("input and output directories must differ")
    if a.num_shards < 1 or not 0 <= a.shard < a.num_shards:
        ap.error("invalid shard")
    if a.batch <= 0 or not 0 <= a.overlap < 1 or a.norm <= 0:
        ap.error("invalid batch, overlap or norm")
    paths = sorted(a.input_dir.glob("*.tif"))[a.shard :: a.num_shards]
    if not paths:
        ap.error("no input tiles for this shard")
    with a.model.open("rb") as fh:
        model_hash = hashlib.file_digest(fh, "sha256").hexdigest()
    provider = PROVIDERS[a.device]
    if problem := device_available(a.device):
        ap.error(problem)
    session = make_session(a.model, a.device, a.tf32)
    if problem := active_provider(session, provider) or options_applied(session, a.device, a.tf32):
        ap.error(problem)
    if problem := model_contract(session):
        ap.error(problem)
    fill = None if a.nodata_fill == "none" else a.nodata_fill
    settings = {"nodata_fill": fill, "tf32": a.tf32, "cuda_sync": a.cuda_sync}
    todo = []
    for src in paths:
        dst = output_path(a.output_dir, src, a.year, a.layout)
        fp = fingerprint(src, model_hash, a.batch, a.overlap, a.norm, provider, **settings)
        if not current(dst, fp):
            todo.append((src, dst, fp))
    cache = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read_stack, todo[0][0]) if todo else None
        for i, (src, dst, fp) in enumerate(todo):
            arr, transform, crs, tags = future.result()
            # Same fail-closed year guard as postprocessing's source_provenance:
            # a tile filed under the wrong year poisons every downstream product.
            if tags.get("year") not in (None, str(a.year)):
                raise RuntimeError(
                    f"{src.name}: input year tag {tags['year']!r} "
                    f"!= --year {a.year}")
            tags.setdefault("year", str(a.year))
            if i + 1 < len(todo):
                future = pool.submit(read_stack, todo[i + 1][0])
            scores, patches = predict_tile(
                session,
                arr,
                batch=a.batch,
                overlap=a.overlap,
                norm=a.norm,
                dev=a.device,
                obuf_cache=cache,
                nodata_fill=fill,
                cuda_sync=a.cuda_sync,
            )
            if fingerprint(src, model_hash, a.batch, a.overlap, a.norm, provider, **settings) != fp:
                raise RuntimeError(f"input changed: {src}")
            write_score(
                dst,
                scores,
                crs,
                transform * Affine.scale(0.25),
                output_tags(
                    tags,
                    model_hash,
                    fp,
                    a.norm,
                    a.overlap,
                    provider,
                    model_name=a.model.name,
                    tile_key=src.stem,
                    **settings,
                ),
            )
            print(f"{src.name}: {patches} patches -> {dst}", flush=True)


if __name__ == "__main__":
    main()
