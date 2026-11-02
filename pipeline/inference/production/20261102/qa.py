"""Per-tile input and output statistics and run provenance, kept as COG tags and a sidecar."""

import hashlib
import json
import os
import platform
import socket
import subprocess
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

NODATA = -32768
SEAM_ROW = 15  # the CDSE mosaic COGs step between rows 15 and 16 on every tile
HERE = Path(__file__).resolve().parent
#: The files whose contents decide the output bits; hashed into the run fingerprint.
CODE_FILES = ("predict.py", "nodata.py", "stream.py", "qa.py")


def input_stats(arr: np.ndarray) -> dict[str, float]:
    """Nodata, zero and bright-pixel shares of a (16, H, W) stack, channel = quarter * 4 + band.

    ``bright_q*`` is the share of valid pixels with blue reflectance >= 0.2 (cloud, haze or snow
    left in the mosaic). ``seam_ratio`` is the median step between rows 15 and 16 over the median
    step of rows 8-24, red band.
    """
    bad = np.zeros(arr.shape[1:], dtype=bool)
    out: dict[str, float] = {}
    for q in range(4):
        bq = np.zeros(arr.shape[1:], dtype=bool)
        for c in range(q * 4, q * 4 + 4):
            bq |= arr[c] == NODATA
        out[f"nodata_q{q + 1}"] = float(bq.mean())
        valid = ~bq
        nv = int(valid.sum())
        bright = ((arr[q * 4 + 2] >= 2000) & valid).sum()
        out[f"bright_q{q + 1}"] = float(bright / nv) if nv else 1.0
        bad |= bq
    out["nodata_any"] = float(bad.mean())
    out["zero"] = float((arr[:, ::4, ::4] == 0).mean())
    top = arr[[0, 4, 8, 12], :25].astype(np.float32)
    top[top == NODATA] = np.nan
    steps = np.abs(np.diff(top, axis=1))
    with np.errstate(all="ignore"):
        prof = np.nanmedian(steps.transpose(1, 0, 2).reshape(24, -1), axis=1)
        others = np.delete(prof[8:24], SEAM_ROW - 8)
        out["seam_ratio"] = float(prof[SEAM_ROW] / np.nanmedian(others))
    return out


def _pct(a: np.ndarray, qs: tuple[int, ...]) -> list[int]:
    h = np.bincount(a.ravel(), minlength=256).cumsum()
    return [int(np.searchsorted(h, h[-1] * q / 100)) for q in qs]


def _longest_run(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return int((np.flatnonzero(d == -1) - np.flatnonzero(d == 1)).max())


def output_stats(out: np.ndarray, patch_rows: int) -> dict[str, float]:
    """Summary of (2, H, W) uint8 field/boundary scores.

    Means and p50/p90/p99 (every 4th pixel), the share of all-zero rows and columns and the longest
    such run (never-written output), the share of 255s, and the mean of the top, second and bottom
    patch rows (``patch_rows`` output rows each).
    """
    f, b = out[0], out[1]
    s: dict[str, float] = {
        "field_mean": float(f.mean(dtype=np.float64) / 255),
        "boundary_mean": float(b.mean(dtype=np.float64) / 255),
    }
    for name, band in (("field", f), ("boundary", b)):
        for q, v in zip((50, 90, 99), _pct(band[::4, ::4], (50, 90, 99)), strict=True):
            s[f"{name}_p{q}"] = v / 255
    s["field_gt_half"] = float((f[::4, ::4] >= 128).mean())
    zr = np.maximum(f.max(axis=1), b.max(axis=1)) == 0
    s["zero_rows"] = float(zr.mean())
    s["zero_cols"] = float((np.maximum(f.max(axis=0), b.max(axis=0)) == 0).mean())
    s["zero_row_run"] = _longest_run(zr)
    s["sat255"] = float((f[::4, ::4] == 255).mean())
    for name, band in (("field", f), ("boundary", b)):
        s[f"top_{name}"] = float(band[:patch_rows].mean() / 255)
        s[f"next_{name}"] = float(band[patch_rows : 2 * patch_rows].mean() / 255)
        s[f"bottom_{name}"] = float(band[-patch_rows:].mean() / 255)
    return s


def to_tags(prefix: str, stats: Mapping[str, object]) -> dict[str, str]:
    """Flat string tags; floats rounded to 6 significant digits."""
    return {
        f"{prefix}{k}": (f"{v:.6g}" if isinstance(v, float) else str(v)) for k, v in stats.items()
    }


@cache
def model_sha256(onnx_path: str) -> str:
    """sha256 over the ONNX file and its external-data file (``.onnx.data``), if any."""
    h = hashlib.sha256()
    for p in (Path(onnx_path), Path(f"{onnx_path}.data")):
        if p.exists():
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 22), b""):
                    h.update(chunk)
    return h.hexdigest()


@cache
def code_sha256(root: str = str(HERE)) -> str:
    """sha256 over ``CODE_FILES``; commits elsewhere in the repo leave the fingerprint alone."""
    h = hashlib.sha256()
    for rel in CODE_FILES:
        h.update(rel.encode() + b"\0" + (Path(root) / rel).read_bytes() + b"\0")
    return h.hexdigest()


@cache
def code_version(root: str = str(HERE)) -> str:
    """Commit of the checkout, with ``-dirty`` if this directory has changes.

    Without git (an EC2 bundle) it is ``FTW_CODE_VERSION``, which the bundler sets to the commit
    it packed.
    """
    try:
        sha = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--", "."],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("FTW_CODE_VERSION", "unknown")
    return f"{sha}-dirty" if dirty else sha


def provenance(
    sess: Any,
    onnx_path: str,
    *,
    nodata_fill: str | None,
    batch: int,
    patch: int,
    overlap: float,
    cuda_sync: bool = True,
    tf32: bool = False,
) -> dict[str, str]:
    """Software, hardware and settings that decide the output bits, plus a run fingerprint.

    The fingerprint hashes everything except the host, GPU and commit: two machines run "the same
    run" only if the code, model, libraries, provider options and settings agree.
    """
    import onnxruntime as ort
    import torch

    opts = {}
    try:
        cuda = sess.get_provider_options().get("CUDAExecutionProvider", {})
        keys = ("cudnn_conv_algo_search", "use_tf32", "cudnn_conv_use_max_workspace")
        opts = {k: cuda[k] for k in keys if k in cuda}
    except AttributeError:
        pass
    if "use_tf32" in opts and opts["use_tf32"] != str(int(tf32)):
        raise RuntimeError(f"session use_tf32={opts['use_tf32']!r}, requested tf32={tf32}")
    sw = {
        "code_sha256": code_sha256(),
        "model_sha256": model_sha256(onnx_path),
        "onnxruntime": ort.__version__,
        "torch": torch.__version__,
        "cuda_runtime": str(torch.version.cuda),
        "cudnn": str(torch.backends.cudnn.version()),
        "provider_options": json.dumps(opts, sort_keys=True),
        "nodata_fill": str(nodata_fill),
        "cuda_sync": str(int(cuda_sync)),
        "tf32": str(int(tf32)),
        "batch": str(batch),
        "patch": str(patch),
        "overlap": str(overlap),
        "norm": "3000",
    }
    hw = {
        "code": code_version(),
        "host": socket.gethostname(),
        "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else "cpu",
        "driver": _driver_version(),
        "python": platform.python_version(),
        "cuda_libs": ";".join(cuda_libs()),
    }
    fp = hashlib.sha256(json.dumps(sw, sort_keys=True).encode()).hexdigest()[:16]
    return {**sw, **hw, "fingerprint": fp}


def cuda_libs() -> list[str]:
    """Real paths of the CUDA, cuDNN and cuBLAS libraries mapped into this process (Linux).

    A cluster module can put another CUDA on ``LD_LIBRARY_PATH``; this shows which one loaded.
    """
    libs: set[str] = set()
    try:
        with open("/proc/self/maps") as fh:
            for line in fh:
                path = line.rsplit(" ", 1)[-1].strip()
                name = os.path.basename(path)
                if name.startswith(("libcudnn", "libcublas", "libcudart", "libcurand", "libnvrtc")):
                    libs.add(os.path.realpath(path))
    except OSError:
        pass
    return sorted(libs)


def _driver_version() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.split("\n")[0].strip()


def write_sidecar(path: Path, record: dict) -> None:
    """Atomic JSON sidecar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(record, sort_keys=True))
    os.replace(tmp, path)
