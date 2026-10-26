"""Golden-crop self-test of the inference path. Run it on every machine type before a run.

    fetch     download the golden crops (golden.CROPS) and, with --full-tile, the whole
              48PWR_0_0 2025 stack into --root; copies the model there
    make-ref  CPU fp32 reference scores for every crop (same predict_tile, CPU provider)
    check     GPU run against the reference; exits 1 if a gate fails:
                gpu_vs_cpu    GPU within TOL of the CPU reference
                repeat        the same session twice: bit-identical
                new_session   a second session (new cuDNN algorithm search): bit-identical
                batch4/1      within TOL_BATCH of the default batch
                top_strip     top patch row mean within TOL_STRIP of the CPU reference
                seams         border step ratio not above the CPU reference's
                full_tile     (--full-tile) the whole tile's corner matches this GPU's crop run
                cross_machine (--against) within TOL of another machine's saved outputs

Checks run with the window31 nodata fill, as production does: unfilled, GPU and CPU differ
by up to ~100/255 on crops with nodata. The full-tile corner is compared unfilled, because the
fill depends on the whole tile.
"""

import argparse
import json
import os
import shutil
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

# predict imports torch, whose CUDA libraries must load before ONNX Runtime's
from predict import BATCH, OVERLAP, PATCH, UP, predict_tile
import golden
from qa import model_sha256, provenance
import onnxruntime as ort

FULL = golden.Crop("full_tile", "48PWR_0_0", 2025, 0, 0, 10008, 10008)
# full-tile pixels covered only by patches that also lie inside the top_left crop
FULL_CMP = tuple((n - golden.NATIVE + golden.STRIDE) * UP
                 for n in (golden.Crop.height, golden.Crop.width))
# uint8 units (1 = 1/255), from the Oct 2026 H100 / A100 runs with TF32 off
TOL = {"max": 8, "mean": 0.05, "gt4": 1e-4}
TOL_BATCH = {"max": 2, "mean": 0.01, "gt4": 0.0}
TOL_TF32 = {"max": 8, "mean": 0.15, "gt4": 1e-3}
TOL_STRIP = 0.005


def _read_crop(c: golden.Crop) -> np.ndarray:
    import rasterio
    from rasterio.windows import Window

    def one(href: str) -> np.ndarray:
        env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "5"}
        with rasterio.Env(**env), rasterio.open(f"/vsicurl/{href}") as d:
            return d.read(1, window=Window.from_slices((c.row, c.row + c.height),
                                                       (c.col, c.col + c.width)))

    with ThreadPoolExecutor(16) as ex:
        return np.stack(list(ex.map(one, golden.band_hrefs(c))))


def fetch(a: argparse.Namespace) -> None:
    (a.root / "inputs").mkdir(parents=True, exist_ok=True)
    (a.root / "model").mkdir(exist_ok=True)
    model = a.root / "model" / Path(a.onnx).name
    if not model.exists():
        shutil.copy2(a.onnx, model)
    for c in [*golden.CROPS, FULL] if a.full_tile else golden.CROPS:
        dst = a.root / "inputs" / f"{c.name}.npy"
        if dst.exists():
            continue
        arr = _read_crop(c)
        np.save(dst.with_suffix(".tmp.npy"), arr)
        os.replace(dst.with_suffix(".tmp.npy"), dst)
        print(f"{c.name}: {c.tile_key} {c.year} {arr.shape} nodata "
              f"{float((arr == -32768).any(0).mean()):.3f}", flush=True)


def _ref_path(root: Path, name: str, fill: str | None) -> Path:
    return root / "ref" / f"{name}_{fill or 'nofill'}.npy"


def make_ref(a: argparse.Namespace) -> None:
    so = ort.SessionOptions()
    so.intra_op_num_threads = a.threads
    cpu = ort.InferenceSession(a.onnx, so, providers=["CPUExecutionProvider"])
    (a.root / "ref").mkdir(parents=True, exist_ok=True)
    meta = {"model": a.onnx, "model_sha256": model_sha256(a.onnx), "onnxruntime": ort.__version__,
            "made": time.strftime("%Y-%m-%dT%H:%M:%S"), "host": socket.gethostname(), "crops": {}}
    for c in golden.CROPS:
        arr = np.load(a.root / "inputs" / f"{c.name}.npy")
        for fill in (None, "window31"):
            out, n = predict_tile(cpu, arr, batch=4, dev="cpu", nodata_fill=fill)
            np.save(_ref_path(a.root, c.name, fill), out)
        meta["crops"][c.name] = {**c.__dict__, "patches": n}
        print(f"{c.name}: {n} patches", flush=True)
    (a.root / "ref" / "meta.json").write_text(json.dumps(meta, indent=1))


def _within(stats: dict[str, float], tol: dict[str, float]) -> bool:
    return all(stats[f"{b}_{k}"] <= v for b in ("field", "boundary") for k, v in tol.items())


def check(a: argparse.Namespace) -> int:
    import torch as th

    from stream import make_session

    meta = json.loads((a.root / "ref" / "meta.json").read_text())
    if (sha := model_sha256(a.onnx)) != meta["model_sha256"]:
        print(f"FAIL model sha256 {sha[:12]} != reference {meta['model_sha256'][:12]}")
        return 1
    fill = None if a.nodata_fill == "none" else a.nodata_fill
    sess, sess2 = make_session(a.onnx, a.tf32), make_session(a.onnx, a.tf32)
    tol = TOL_TF32 if a.tf32 else TOL
    tol_batch = TOL_TF32 if a.tf32 else TOL_BATCH
    prov = provenance(sess, a.onnx, nodata_fill=fill, batch=BATCH, patch=PATCH, overlap=OVERLAP,
                      tf32=a.tf32)

    def run(s, arr: np.ndarray, batch: int, f: str | None = fill) -> np.ndarray:
        return predict_tile(s, arr, batch=batch, obuf_cache={}, nodata_fill=f)[0]

    rows, ok = [], True
    gpu = th.cuda.get_device_name().replace(" ", "_")
    save = a.out or a.root / "gpu_outputs" / f"{socket.gethostname()}_{gpu}_{fill or 'nofill'}"
    save.mkdir(parents=True, exist_ok=True)

    def gate(crop: str, name: str, passed: bool, detail: object) -> None:
        nonlocal ok
        ok &= passed
        rows.append({"crop": crop, "check": name, "pass": passed, "detail": detail})
        print(f"{'PASS' if passed else 'FAIL'} {crop:13s} {name:13s} {detail}", flush=True)

    for c in golden.CROPS:
        arr = np.load(a.root / "inputs" / f"{c.name}.npy")
        ref = np.load(_ref_path(a.root, c.name, fill))
        t = time.perf_counter()
        o = run(sess, arr, BATCH)
        secs = time.perf_counter() - t
        d = golden.diff_stats(o, ref)
        gate(c.name, "gpu_vs_cpu", _within(d, tol), d)
        gate(c.name, "repeat", bool(np.array_equal(o, run(sess, arr, BATCH))), f"{secs:.1f}s")
        o2 = run(sess2, arr, BATCH)
        gate(c.name, "new_session", bool(np.array_equal(o, o2)), golden.diff_stats(o, o2))
        for b in (4, 1):
            db = golden.diff_stats(o, run(sess, arr, b))
            gate(c.name, f"batch{b}", _within(db, tol_batch), db)
        sg, sr = golden.strip_means(o), golden.strip_means(ref)
        gate(c.name, "top_strip", max(abs(sg[k] - sr[k]) for k in sg) <= TOL_STRIP,
             {"gpu": sg, "cpu": sr})
        rg, rr = golden.border_seam_ratio(o), golden.border_seam_ratio(ref)
        gate(c.name, "seams", rg <= max(1.1, rr * 1.05), {"gpu": round(rg, 3), "cpu": round(rr, 3)})
        np.save(save / f"{c.name}.npy", o)
        if a.against:
            dm = golden.diff_stats(o, np.load(a.against / f"{c.name}.npy"))
            gate(c.name, "cross_machine", _within(dm, tol), dm)
    if a.full_tile:
        h, w = FULL_CMP
        crop = run(sess, np.load(a.root / "inputs" / "top_left.npy"), BATCH, None)
        full = run(sess, np.load(a.root / "inputs" / "full_tile.npy"), BATCH, None)
        d = golden.diff_stats(full[:, :h, :w], crop[:, :h, :w])
        gate("full_tile", "corner", _within(d, tol_batch), d)
    report = {"ok": ok, "provenance": prov, "checks": rows}
    (save / "check.json").write_text(json.dumps(report, indent=1, default=str))
    print(f"{'ALL PASS' if ok else 'FAILED'} -> {save / 'check.json'}", flush=True)
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("fetch", "make-ref", "check"))
    ap.add_argument("--root", type=Path, default=os.environ.get("FTW_GOLDEN"))
    ap.add_argument("--onnx", help="default: the copy under ROOT/model/")
    ap.add_argument("--full-tile", action="store_true")
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 8)))
    ap.add_argument("--nodata-fill", default="window31", choices=("none", "window31"))
    ap.add_argument("--tf32", action="store_true")
    ap.add_argument("--against", type=Path, help="check: another machine's gpu_outputs/<dir>")
    ap.add_argument("--out", type=Path, help="check: where to save outputs")
    a = ap.parse_args()
    if a.root is None:
        ap.error("--root or FTW_GOLDEN is required")
    a.root = Path(a.root)
    if a.onnx is None:
        models = sorted((a.root / "model").glob("*.onnx"))
        if a.step == "fetch" or len(models) != 1:
            ap.error("--onnx is required (fetch, or ROOT/model/ does not hold exactly one model)")
        a.onnx = str(models[0])
    if a.step == "fetch":
        fetch(a)
    elif a.step == "make-ref":
        make_ref(a)
    else:
        sys.exit(check(a))


if __name__ == "__main__":
    main()
