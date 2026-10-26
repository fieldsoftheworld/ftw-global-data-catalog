import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
from affine import Affine

import golden
import qa
from predict import NATIVE, OVERLAP, UP
from qa import NODATA, input_stats, model_sha256, output_stats, provenance, to_tags
from stream import MODEL_BANDS, QUARTERS, check_grid, is_stale, source_items, work_list, write_tile


def _stack(h=64, w=96):
    return np.random.default_rng(0).integers(400, 1500, (16, h, w)).astype(np.int16)


def test_work_list_keeps_complete_keep_tiles_in_model_order(tmp_path):
    rows = []
    tiles = (("31UFS_0_0", QUARTERS), ("31UFT_0_0", QUARTERS[:3]), ("32TQM_0_0", QUARTERS))
    for tk, quarters in tiles:
        for q in quarters:
            row = {"tile_key": tk, "quarter": q}
            for i, b in enumerate(("b02", "b03", "b04", "b08")):
                row[f"{b}_href"] = f"https://m/{tk}/{q}/{b}.tif"
                row[f"{b}_size"] = 100 + i
            rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "tile_index_2025.parquet")
    keep = tmp_path / "keep.txt"
    keep.write_text("31UFS_0_0\n31UFT_0_0\n")
    jobs = work_list(tmp_path / "tile_index_2025.parquet", keep)
    assert [j[0] for j in jobs] == ["31UFS_0_0"]  # 31UFT lacks Q4, 32TQM is not kept
    hrefs = jobs[0][1]
    assert len(hrefs) == 16
    assert hrefs[:4] == [f"https://m/31UFS_0_0/Q1/{b}.tif" for b in MODEL_BANDS]
    assert hrefs[-1] == "https://m/31UFS_0_0/Q4/b08.tif"


def test_check_grid_rejects_a_misregistered_band():
    g = (Affine(10, 0, 0, 0, -10, 0), "EPSG:32631", (8, 8), "int16", -32768)
    check_grid("t", [g] * 16)
    with pytest.raises(ValueError, match="band 3"):
        check_grid("t", [g] * 3 + [(Affine(10, 0, 10, 0, -10, 0), *g[1:])] + [g] * 12)
    with pytest.raises(ValueError, match="int16"):
        check_grid("t", [(*g[:3], "uint16", 0)] * 16)


def test_source_items_name_the_four_quarterly_mosaics():
    assert source_items(2025, "31UFS_0_0").split(",") == [
        f"Sentinel-2_mosaic_2025_{q}_31UFS_0_0" for q in QUARTERS
    ]


def test_write_tile_writes_the_cog_tags_and_sidecar(tmp_path):
    dst = tmp_path / "raster/2025/zone=31/gzd=31U/31UFS_0_0/31UFS_0_0.tif"
    side = tmp_path / "_qa/2025/31UFS_0_0.json"
    out = np.full((2, 256, 256), 100, np.uint8)
    tags = {"tile_key": "31UFS_0_0", **to_tags("prov_", {"fingerprint": "abc"})}
    write_tile(dst, side, out, "EPSG:32631", Affine(2.5, 0, 500000, 0, -2.5, 5000000), tags,
               {"in": {"nodata_any": 0.0}, "prov": {"fingerprint": "abc"}}, 128)
    with rasterio.open(dst) as ds:
        t = ds.tags()
        assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        np.testing.assert_array_equal(ds.read(), out)
    assert t["prov_fingerprint"] == "abc"
    assert t["qa_in_nodata_any"] == "0"
    assert float(t["qa_field_mean"]) == pytest.approx(100 / 255, abs=1e-5)
    assert json.loads(side.read_text())["out"]["field_gt_half"] == 0.0
    assert not is_stale(dst, "abc")
    assert is_stale(dst, "other")
    assert not is_stale(tmp_path / "missing.tif", "abc")


def test_input_stats_nodata_per_quarter_and_any():
    arr = _stack()
    arr[4:8, :32] = NODATA
    arr[13, :, :48] = NODATA
    s = input_stats(arr)
    assert (s["nodata_q1"], s["nodata_q2"], s["nodata_q4"], s["nodata_any"]) == (0, 0.5, 0.5, 0.75)


def test_input_stats_flags_the_mosaic_seam_row():
    arr = _stack()
    clean = input_stats(arr)["seam_ratio"]
    arr[:, :16] += 600
    assert input_stats(arr)["seam_ratio"] > 1.5 * clean


def test_output_stats_zero_rows_and_patch_rows():
    out = np.full((2, 1024, 64), 7, np.uint8)
    out[:, 896:] = 0  # the bottom patch row never written
    s = output_stats(out, 128)
    assert s["zero_row_run"] == 128
    assert np.isclose(s["zero_rows"], 128 / 1024)
    assert s["field_p50"] == 7 / 255
    assert s["top_field"] == pytest.approx(7 / 255)
    assert s["bottom_field"] == 0


def test_model_sha_covers_external_data(tmp_path):
    m = tmp_path / "m.onnx"
    m.write_bytes(b"graph")
    first = model_sha256.__wrapped__(str(m))
    (tmp_path / "m.onnx.data").write_bytes(b"weights")
    assert model_sha256.__wrapped__(str(m)) != first


class _Sess:
    def __init__(self, tf32=None):
        opts = {"cudnn_conv_algo_search": "EXHAUSTIVE"}
        if tf32 is not None:
            opts["use_tf32"] = tf32
        self.opts = {"CUDAExecutionProvider": opts}

    def get_provider_options(self):
        return self.opts


def test_provenance_fingerprint_follows_the_settings(tmp_path):
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    kw = {"nodata_fill": "window31", "batch": 8, "patch": 1536, "overlap": 0.5}
    base = provenance(_Sess(), str(onnx), **kw)
    assert provenance(_Sess(), str(onnx), **kw, tf32=True)["fingerprint"] != base["fingerprint"]
    other = provenance(_Sess(), str(onnx), **{**kw, "patch": 512})
    assert other["fingerprint"] != base["fingerprint"]
    with pytest.raises(RuntimeError, match="use_tf32"):
        provenance(_Sess("1"), str(onnx), **kw)


def test_code_sha256_tracks_only_the_inference_files(tmp_path):
    for rel in qa.CODE_FILES:
        (tmp_path / rel).write_text("a")
    first = qa.code_sha256.__wrapped__(str(tmp_path))
    (tmp_path / "README.md").write_text("docs")
    assert qa.code_sha256.__wrapped__(str(tmp_path)) == first
    (tmp_path / qa.CODE_FILES[0]).write_text("b")
    assert qa.code_sha256.__wrapped__(str(tmp_path)) != first


def test_golden_grid_is_the_inference_grid():
    assert round(NATIVE * (1 - OVERLAP)) == golden.STRIDE
    for c in golden.CROPS:
        assert (c.height - NATIVE) % golden.STRIDE == 0
        assert (c.width - NATIVE) % golden.STRIDE == 0
        assert c.row + c.height <= 10008 and c.col + c.width <= 10008


def test_border_seam_ratio_sees_a_step_at_every_patch_start():
    smooth = np.random.default_rng(0).integers(100, 103, (2, 4096, 4096)).astype(np.uint8)
    assert golden.border_seam_ratio(smooth) < 1.1
    stepped = smooth.copy()
    for k in range(0, 4096, golden.STRIDE * UP):
        stepped[0, k:] += 6
    assert golden.border_seam_ratio(stepped) > 1.3
