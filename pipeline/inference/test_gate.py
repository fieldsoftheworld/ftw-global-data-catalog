import argparse
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
import rasterio.shutil
from rasterio.enums import Resampling
from rasterio.transform import from_origin

import cog_checks as cc
import gate
from qa import output_stats, to_tags
from run import OVERVIEWS, write_score

N = 2600  # not a multiple of 64: the last overview level has fractional source weights
TK, YEAR, FP = "15TVG_0_0", 2025, "fp1"
ORIGIN = (500000.0, 4600020.0)
PROV = {"fingerprint": FP, "cuda_sync": "1", "tf32": "0", "nodata_fill": "window31",
        "patch": "1536", "overlap": "0.5", "code": "96f6d7113a39", "gpu": "NVIDIA H100 80GB HBM3"}


@pytest.fixture(autouse=True)
def _small(monkeypatch):
    monkeypatch.setattr(cc, "SIZE", N)


def _scores(seed=0):
    rng = np.random.default_rng(seed)
    a = np.zeros((2, N, N), np.uint8)
    a[0, 200:1800, 300:2400] = rng.integers(0, 256, (1600, 2100), dtype=np.uint8)
    a[1, 200:1800, 300:2400] = rng.integers(0, 64, (1600, 2100), dtype=np.uint8)
    return a


def _even(seed=0):
    return np.random.default_rng(seed).integers(0, 256, (2, N, N), dtype=np.uint8)


def _cog(root, tk=TK, arr=None, **prov):
    arr = _scores() if arr is None else arr
    p = root / "raster" / str(YEAR) / f"zone={tk[:2]}" / f"gzd={tk[:3]}" / tk / f"{tk}.tif"
    pr = {**PROV, **prov}
    out = output_stats(arr, 1536)
    tags = {"tile_key": tk, "source_items": gate.source_items(YEAR, tk),
            **to_tags("prov_", pr), **to_tags("qa_", out), "qa_in_nodata_any": "0"}
    write_score(p, arr, "EPSG:32615", from_origin(*ORIGIN, 2.5, 2.5), tags)
    side = root / "_qa" / str(YEAR) / f"{tk}.json"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({
        "in": {"transform": f"10.00,0.00,{ORIGIN[0]:.2f},0.00,-10.00,{ORIGIN[1]:.2f}"},
        "out": out, "prov": pr,
    }))
    return p


def _args(root, **kw):
    base = {"year": YEAR, "root": root, "gpu": ["H100", "A100"], "nodata_fill": "window31",
            "patch": 1536, "overlap": 0.5, "allow_tf32": False, "fingerprint": FP, "seed": 0,
            "history": None}
    return argparse.Namespace(**{**base, **kw})


def _errors(p, root, full=True, **kw):
    return gate.validate_one(str(p), _args(root, **kw), full)["errors"]


def test_good_cog_passes(tmp_path):
    p = _cog(tmp_path)
    rec = gate.validate_one(str(p), _args(tmp_path), True)
    assert rec["errors"] == ""
    assert rec["ovr_max_diff"] == 0


def test_wrong_fingerprint_and_settings_fail(tmp_path):
    p = _cog(tmp_path)
    assert "prov_fingerprint" in _errors(p, tmp_path, fingerprint="other")
    assert "patch" in _errors(p, tmp_path, patch=512)


def test_truncated_file_fails(tmp_path):
    p = _cog(tmp_path)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) - 4096])
    assert _errors(p, tmp_path)


def _rebuild(p, tmp_path, base, ovr_from):
    """Overviews built from ``ovr_from``, then the base overwritten with ``base``."""
    stage = tmp_path / "bad_stage.tif"
    with rasterio.open(p) as src:
        prof = {**src.profile, "driver": "GTiff"}
        tags = src.tags()
    with rasterio.open(stage, "w", **prof) as d:
        d.write(ovr_from)
        d.build_overviews(list(OVERVIEWS), Resampling.average)
        d.update_tags(**tags)
        d.descriptions = ("field", "boundary")
        d.scales = (1 / 255, 1 / 255)
    with rasterio.open(stage, "r+") as d:
        d.write(base)
    rasterio.shutil.copy(stage, p, driver="COG", COMPRESS="ZSTD", PREDICTOR="2",
                         BLOCKSIZE="512", OVERVIEWS="FORCE_USE_EXISTING")


def test_empty_overviews_fail(tmp_path):
    p = _cog(tmp_path)
    arr = _scores()
    _rebuild(p, tmp_path, base=arr, ovr_from=np.zeros_like(arr))
    err = _errors(p, tmp_path, full=False)
    assert "max diff" in err or "empty" in err


def test_full_decode_catches_a_changed_base(tmp_path):
    p = _cog(tmp_path)
    other = _scores(seed=1)
    _rebuild(p, tmp_path, base=other, ovr_from=other)
    err = _errors(p, tmp_path, full=True)
    assert "mean" in err or "statistics" in err


def test_layout_rejects_a_plain_geotiff(tmp_path):
    p = tmp_path / "plain.tif"
    with rasterio.open(p, "w", driver="GTiff", count=2, width=N, height=N, dtype="uint8",
                       tiled=True, blockxsize=512, blockysize=512, compress="ZSTD") as ds:
        ds.write(_scores())
    with rasterio.open(p) as ds:
        assert any("layout" in e for e in cc.layout_errors(ds, p.stat().st_size))


def test_size_flags():
    df = pd.DataFrame({"bytes": [100, 100, 100, 3, 2000], "errors": [""] * 5})
    out = gate.size_flags(df)
    assert out.errors.tolist()[:3] == ["", "", ""]
    assert "size" in out.errors[3] and "size" in out.errors[4]


def test_expected_epsg():
    assert cc.expected_epsg("15TVG_0_0") == 32615
    assert cc.expected_epsg("22KFD_0_0") == 32722


def _index(d):
    rows = [{"tile_key": tk, "quarter": q}
            for tk, qs in ((TK, "Q1 Q2 Q3 Q4"), ("15TVH_0_0", "Q1 Q2 Q3 Q4"),
                           ("15TVJ_0_0", "Q1 Q2 Q3"), ("15TVK_0_0", "Q3"))
            for q in qs.split()]
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / f"tile_index_{YEAR}.parquet")


def test_scan_flags_completeness_and_provenance(tmp_path):
    root, idx = tmp_path / "out", tmp_path / "index"
    _index(idx)
    keep = tmp_path / "keep.txt"
    keep.write_text(f"{TK}\n15TVH_0_0\n15TVJ_0_0\n15TVK_0_0\n15TVL_0_0\n")
    known = tmp_path / "no_mosaic.csv"
    known.write_text(f"year,tile_key,quarters,reason\n{YEAR},15TVK_0_0,Q3,x\n{YEAR},15TVL_0_0,,x\n")
    _cog(root, arr=_even())
    _cog(root, "15TVM_0_0", arr=_even(1), tf32="1")  # outside the keep-list, and TF32 on
    leftover = root / "raster" / str(YEAR) / "zone=15/gzd=15T" / TK / f"{TK}.tif.tmp-1"
    leftover.write_bytes(b"x")
    out = tmp_path / "scan.csv"
    a = _args(root, index_dir=idx, keep_list=keep, no_mosaic=known, fingerprint=None,
              workers=2, out=out)
    assert gate.scan(a) == 1
    flags = pd.read_csv(out).fillna("").set_index("tile_key").qa_flags
    assert flags[TK] == ""
    assert flags["15TVH_0_0"] == "missing"
    assert flags["15TVJ_0_0"] == "no_input_unknown"
    assert "15TVK_0_0" not in flags and "15TVL_0_0" not in flags
    assert flags["15TVM_0_0"] == "tf32"
    assert flags[f"{TK}.tif.tmp-1"] == "tmp_leftover"


def test_flag_rows():
    rec = {"tile_key": "t", "path": "/x/t.tif", "structure": "", **{f"prov_{k}": v
           for k, v in PROV.items()}, "zero_row_run": 0, "in_nodata_any": 0.0,
           "top_field": 0.3, "next_field": 0.3, "top_boundary": 0.1, "next_boundary": 0.1,
           "sat255": 0.0, "field_mean": 0.3}
    df = pd.DataFrame([
        rec, {**rec, "tile_key": "ok2"},
        {**rec, "tile_key": "stale", "prov_fingerprint": "other"},
        {**rec, "tile_key": "l40s", "prov_gpu": "NVIDIA L40S"},
        {**rec, "tile_key": "p512", "prov_patch": "512"},
        {**rec, "tile_key": "noov", "prov_overlap": np.nan},
        {**rec, "tile_key": "trunc", "zero_row_run": 4000},
        {**rec, "tile_key": "ocean", "zero_row_run": 4000, "in_nodata_any": 0.8},
        {**rec, "tile_key": "edge", "top_field": 0.05},
        {**rec, "tile_key": "bad", "structure": "levels=0"},
    ])
    f = gate.flag(df, _args(None, fingerprint=None)).set_index("tile_key").qa_flags
    assert f["t"] == ""
    assert f["stale"] == "stale"
    assert f["l40s"] == "gpu"
    assert f["p512"] == "settings"
    assert f["noov"] == "settings"
    assert f["trunc"] == "zero_run"
    assert f["ocean"] == ""
    assert f["edge"] == "top_edge"
    assert f["bad"] == "structure"


def test_summary_passes_only_a_clean_complete_year(tmp_path):
    scan = tmp_path / "scan.csv"
    pd.DataFrame([
        {"tile_key": "a", "path": "/x/a.tif", "qa_flags": "", "prov_fingerprint": FP,
         "prov_gpu": "NVIDIA H100", "field_mean": 0.3},
        {"tile_key": "b", "path": "/x/b.tif", "qa_flags": "top_edge", "prov_fingerprint": FP,
         "prov_gpu": "NVIDIA A100", "field_mean": 0.2},
    ]).to_csv(scan, index=False)
    vd = tmp_path / "val"
    vd.mkdir()
    val = pd.DataFrame({"tile_key": ["a", "b"], "errors": ["", ""], "full_decode": [True, False],
                        "ovr_max_diff": [0, 0], "size_warn": [False, False], "bytes": [10, 11]})
    val.to_csv(vd / "shard_0.csv", index=False)
    a = argparse.Namespace(year=YEAR, scan=scan, validate_dir=vd, release=None,
                           out=tmp_path / "gate.json")
    assert gate.summary(a) == 0
    assert json.loads(a.out.read_text())["gpu"] == {"H100": 1, "A100": 1}
    val.iloc[:1].to_csv(vd / "shard_0.csv", index=False)  # b was never validated
    assert gate.summary(a) == 1
