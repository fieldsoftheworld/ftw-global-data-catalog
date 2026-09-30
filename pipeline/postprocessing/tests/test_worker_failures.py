import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pool_utils
import simplify_polygons as sp
import terrain as tfa


def _boom(x: int) -> int:
    if x % 3 == 0:
        raise ValueError(f"bad {x}")
    return x


def test_drain_finishes_all_then_exit_nonzero(capsys):
    ok = []
    with ThreadPoolExecutor(2) as pool:
        futs = {pool.submit(_boom, i): f"t{i}" for i in range(1, 8)}
        failures = pool_utils.drain(futs, lambda k, r: ok.append(k))
    assert sorted(ok) == ["t1", "t2", "t4", "t5", "t7"]
    assert sorted(k for k, _ in failures) == ["t3", "t6"]
    with pytest.raises(SystemExit) as e:
        pool_utils.exit_on_failures(failures, 7, "tiles")
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "ValueError: bad 3" in err
    assert "2/7 tiles failed" in err


def test_warp_missing_local_file_is_nan(tmp_path):
    out = tfa._warp(
        [str(tmp_path / "nope.tif")], (2, 2), "EPSG:4326", None, Resampling.nearest, np.float32
    )
    assert np.isnan(out).all()


def test_warp_transport_error_raises(monkeypatch):
    def fail(*a: object, **k: object) -> None:
        raise RasterioIOError("CURL error: Could not resolve host: example.com")

    monkeypatch.setattr(tfa.rasterio, "open", fail)
    with pytest.raises(RasterioIOError, match="CURL"):
        tfa._warp(["/vsicurl/x.tif"], (2, 2), "EPSG:4326", None, Resampling.nearest, np.uint8)


def test_warp_http_404_is_missing(monkeypatch):
    def gone(*a: object, **k: object) -> None:
        raise RasterioIOError("HTTP response code: 404")

    monkeypatch.setattr(tfa.rasterio, "open", gone)
    out = tfa._warp(["/vsicurl/x.tif"], (2, 2), "EPSG:4326", None, Resampling.nearest, np.uint8)
    assert (out == 0).all()


def _src(path: Path, n: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"a": list(range(n))}), path)


def test_simplify_reuse_needs_matching_fingerprint(tmp_path):
    src, dst = tmp_path / "in/2020/T.parquet", tmp_path / "out/2020/T.parquet"
    _src(src)
    dst.parent.mkdir(parents=True)
    assert not sp.is_current(dst, src, 5.0)
    sp.write_atomic(pq.read_table(src), dst, sp.fingerprint(src, 5.0))
    assert sp.is_current(dst, src, 5.0)
    assert not sp.is_current(dst, src, 2.0)
    _src(src, n=5)
    os.utime(src, ns=(1, 1))
    assert not sp.is_current(dst, src, 5.0)


def test_simplify_rejects_truncated_and_unstamped_outputs(tmp_path):
    src, dst = tmp_path / "in/T.parquet", tmp_path / "out/T.parquet"
    _src(src)
    dst.parent.mkdir(parents=True)
    _src(dst)
    assert not sp.is_current(dst, src, 5.0)
    dst.write_bytes(b"PAR1 truncated")
    assert not sp.is_current(dst, src, 5.0)


def test_simplify_empty_table_written_atomically(tmp_path):
    src, dst = tmp_path / "in/T.parquet", tmp_path / "out/T.parquet"
    _src(src, n=0)
    dst.parent.mkdir(parents=True)
    sp.write_atomic(pq.read_table(src), dst, sp.fingerprint(src, 5.0))
    assert sp.is_current(dst, src, 5.0)
    assert not list(dst.parent.glob("*.tmp-*"))
