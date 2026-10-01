"""Run an FP32 ONNX model over stacked quarterly mosaic tiles."""

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import torch  # noqa: F401 - preload its CUDA libraries before ONNX Runtime
import onnxruntime as ort
import rasterio
from affine import Affine
from predict import predict_tile

PROVIDERS = {"cuda": "CUDAExecutionProvider", "cpu": "CPUExecutionProvider"}


def read_stack(path: Path):
    with rasterio.open(path) as ds:
        if ds.count != 16 or ds.crs is None:
            raise ValueError(f"{path}: expected 16 georeferenced bands")
        if not np.isclose(ds.transform.a, 10) or not np.isclose(ds.transform.e, -10):
            raise ValueError(f"{path}: expected north-up 10 m pixels")
        if not np.isclose(ds.transform.b, 0) or not np.isclose(ds.transform.d, 0):
            raise ValueError(f"{path}: rotated input grid")
        expected = tuple(
            f"{q}_{b}" for q in ("Q1", "Q2", "Q3", "Q4") for b in ("B04", "B03", "B02", "B08")
        )
        if any(ds.descriptions) and ds.descriptions != expected:
            raise ValueError(f"{path}: unexpected band order")
        return ds.read().astype(np.float32), ds.transform, ds.crs, ds.tags()


def fingerprint(
    src: Path, model_hash: str, batch: int, overlap: float, norm: float, provider: str
) -> str:
    st = src.stat()
    return json.dumps([st.st_size, st.st_mtime_ns, model_hash, batch, overlap, norm, provider])


def output_tags(
    src_tags: dict, model_hash: str, fp: str, norm: float, overlap: float, provider: str
) -> dict:
    """Source tags plus this run's provenance."""
    return dict(
        src_tags,
        model_sha256=model_hash,
        inference_fingerprint=fp,
        execution_provider=provider,
        input_bands="Q1,Q2,Q3,Q4 x B04,B03,B02,B08",
        normalization=str(norm),
        overlap=str(overlap),
    )


def current(dst: Path, fp: str) -> bool:
    if not dst.exists():
        return False
    try:
        with rasterio.open(dst) as ds:
            return ds.count == 2 and ds.tags().get("inference_fingerprint") == fp
    except rasterio.errors.RasterioIOError:
        return False


def write_score(dst: Path, scores: np.ndarray, crs, transform, tags: dict) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}")
    with rasterio.open(
        tmp,
        "w",
        driver="COG",
        height=scores.shape[1],
        width=scores.shape[2],
        count=2,
        dtype="uint8",
        crs=crs,
        transform=transform,
        compress="ZSTD",
        level=9,
        predictor=2,
        blocksize=512,
        BIGTIFF="IF_SAFER",
        overview_resampling="average",
    ) as ds:
        ds.write(scores)
        ds.scales = (1 / 255, 1 / 255)
        ds.set_band_description(1, "field")
        ds.set_band_description(2, "boundary")
        ds.update_tags(**tags)
    os.replace(tmp, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--overlap", type=float, default=0.25)
    ap.add_argument("--norm", type=float, default=3000)
    ap.add_argument("--device", choices=tuple(PROVIDERS), default="cuda")
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
    if provider not in ort.get_available_providers():
        ap.error(f"{provider} unavailable")
    session = ort.InferenceSession(str(a.model), providers=[provider])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].name != "input" or inputs[0].type != "tensor(float)":
        ap.error("model must take FP32 'input'")
    if not outputs or outputs[0].name != "logits" or outputs[0].type != "tensor(float)":
        ap.error("model must emit FP32 'logits'")
    if inputs[0].shape[1:] != [16, 512, 512] or outputs[0].shape[1:] != [3, 512, 512]:
        ap.error("expected dynamic-batch input [B,16,512,512] and logits [B,3,512,512]")
    todo = []
    for src in paths:
        dst = a.output_dir / src.name
        fp = fingerprint(src, model_hash, a.batch, a.overlap, a.norm, provider)
        if not current(dst, fp):
            todo.append((src, dst, fp))
    cache = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read_stack, todo[0][0]) if todo else None
        for i, (src, dst, fp) in enumerate(todo):
            arr, transform, crs, tags = future.result()
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
            )
            if fingerprint(src, model_hash, a.batch, a.overlap, a.norm, provider) != fp:
                raise RuntimeError(f"input changed: {src}")
            write_score(
                dst,
                scores,
                crs,
                transform * Affine.scale(0.25),
                output_tags(tags, model_hash, fp, a.norm, a.overlap, provider),
            )
            print(f"{src.name}: {patches} patches -> {dst}", flush=True)


if __name__ == "__main__":
    main()
