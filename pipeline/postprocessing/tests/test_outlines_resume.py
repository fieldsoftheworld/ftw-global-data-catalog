"""Outline resume, output schema and tile-list handling."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio

PP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PP))

import outlines as ol


def _score_cog(path: Path, tags: dict | None = None, size: int = 64) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((2, size, size), np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="COG",
        height=size,
        width=size,
        count=2,
        dtype="uint8",
        crs="EPSG:32631",
        transform=rasterio.Affine(2.5, 0, 400000 - 5000, 0, -2.5, 5400000 + 5000),
    ) as ds:
        ds.write(data)
        if tags:
            ds.update_tags(**tags)
    return path


def _fp(src: Path, **over) -> str:
    kw = dict(year=2025, core=8192, halo=512, backend="exact", simplify_m=0.0)
    kw.update(over)
    return ol.fingerprint(src, **kw)


def _stamp(dst: Path, fp: str, prov: dict | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tbl = pa.Table.from_pylist([], schema=ol.SCHEMA).replace_schema_metadata(
        {
            ol.FP_KEY: fp.encode(),
            ol.PROVENANCE_KEY: json.dumps(prov or {}).encode(),
            b"geo": ol.GEO_META,
        }
    )
    pq.write_table(tbl, dst)


def test_resume_requires_a_matching_fingerprint(tmp_path):
    """Resume used to test only that the output exists.

    A regenerated score COG or a changed flag then published the previous run's
    parcels. Built like PR2's run.py inference_fingerprint and
    simplify_polygons' simplify_fingerprint.
    """
    src = _score_cog(tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif")
    dst = tmp_path / "outlines/2025/31UFS.parquet"
    assert not ol.is_current(dst, _fp(src))
    _stamp(dst, _fp(src))
    assert ol.is_current(dst, _fp(src))


@pytest.mark.parametrize(
    "over",
    [
        {"core": 4096},
        {"halo": 256},
        {"backend": "fast"},
        {"simplify_m": 5.0},
        {"year": 2024},
    ],
)
def test_every_result_affecting_flag_is_in_the_fingerprint(tmp_path, over):
    src = _score_cog(tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif")
    dst = tmp_path / "outlines/2025/31UFS.parquet"
    _stamp(dst, _fp(src))
    assert not ol.is_current(dst, _fp(src, **over)), over


def test_a_regenerated_score_cog_is_not_current(tmp_path):
    src = _score_cog(tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif")
    dst = tmp_path / "outlines/2025/31UFS.parquet"
    _stamp(dst, _fp(src))
    assert ol.is_current(dst, _fp(src))
    _score_cog(src, size=96)
    assert not ol.is_current(dst, _fp(src))


def test_the_cogs_inference_fingerprint_identifies_the_source(tmp_path):
    "Prefer PR2's tag: it identifies the model and inputs, not just the bytes."
    src = _score_cog(tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif", tags={"inference_fingerprint": "A"})
    fp_a = _fp(src)
    assert "A" in fp_a
    _score_cog(src, tags={"inference_fingerprint": "B"})
    assert _fp(src) != fp_a


@pytest.mark.parametrize("corrupt", [b"", b"PAR1 truncated"])
def test_truncated_or_unstamped_output_is_not_current(tmp_path, corrupt):
    src = _score_cog(tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif")
    dst = tmp_path / "outlines/2025/31UFS.parquet"
    dst.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([], schema=ol.SCHEMA), dst)  # unstamped
    assert not ol.is_current(dst, _fp(src))
    dst.write_bytes(corrupt)
    assert not ol.is_current(dst, _fp(src))


def test_schema_is_declared_not_inferred_from_the_first_tile():
    """pa.Table.from_pylist(rows) inferred the schema from whichever tile finished.

    A column that happened to be all-NULL in one tile came out a different type
    there, so tiles written by different workers disagreed.
    """
    assert ol.SCHEMA.names[0] == "tile_key"
    assert ol.SCHEMA.names[-1] == "geometry"
    assert ol.SCHEMA.field("slope_mean").type == pa.float64()
    assert ol.SCHEMA.field("geometry").type == pa.binary()
    # the empty-tile path and the populated path must agree exactly
    empty = pa.Table.from_pylist([], schema=ol.SCHEMA)
    assert empty.schema.equals(ol.SCHEMA)
    import shapely

    row = {
        "tile_key": "31UFS",
        "year": 2025,
        "parcel_id": 1,
        "area_m2": 1000.0,
        "n_parts": 1,
        "ext_h_px": 10,
        "ext_w_px": 12,
        "touches_window_edge": False,
        "in_utm_zone": True,
        "in_mgrs_square": True,
        **{
            k: float("nan")
            for k in (
                "pf_mean",
                "pb_mean",
                "frac_nodata_1q",
                "frac_nodata_3q",
                "frac_water",
                "frac_crops_ever",
                "slope_mean",
                "frac_slope_gt30",
                "elev_mean",
            )
        },
    }
    g = np.array([shapely.box(3, 51, 3.001, 51.001)], dtype=object)
    b = shapely.bounds(g)
    cols = {n: [row[n]] for n in ol.SCHEMA.names if n in row}
    for j, k in enumerate(("xmin", "ymin", "xmax", "ymax")):
        cols[k] = b[:, j]
    cols["geometry"] = shapely.to_wkb(g)
    assert pa.table(cols, schema=ol.SCHEMA).schema.equals(empty.schema)


def test_geo_footer_is_present_so_duckdb_can_read_the_geometry():
    geo = json.loads(ol.GEO_META)
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["encoding"] == "WKB"


def test_year_mismatch_between_cog_and_run_fails(tmp_path):
    src = _score_cog(tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif", tags={"year": "2024"})
    with pytest.raises(ValueError, match="score COG is year 2024, run asked for 2025"):
        ol.source_provenance(src, 2025)


def test_provenance_carries_the_model_identity(tmp_path):
    src = _score_cog(
        tmp_path / "staging-data/raster/2025/31UFS/31UFS.tif",
        tags={"model_sha256": "deadbeef", "input_bands": "Q1,Q2,Q3,Q4 x B04,B03,B02,B08"},
    )
    prov = ol.source_provenance(src, 2025)
    assert prov["model_sha256"] == "deadbeef"
    assert "B04" in prov["input_bands"]


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PP / "outlines.py"), "--year", "2025", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_an_empty_tile_list_file_means_zero_tiles_not_every_tile(tmp_path):
    """An empty list is falsy, so `names or glob(...)` globbed the whole year.

    The caller asked for nothing and would have got ~7,467 tiles; the
    "no input scores" guard could not fire either.
    """
    for tk in ("31UFS", "31UFT", "31UFU"):
        _score_cog(tmp_path / f"staging-data/raster/2025/{tk}/{tk}.tif")
    (tmp_path / "empty-list.txt").write_text("")
    r = _run(tmp_path, "--tile-list", "empty-list.txt")
    assert r.returncode != 0
    assert "no input scores" in r.stdout + r.stderr
    assert "3 tiles" not in r.stdout


def test_a_populated_tile_list_is_honoured(tmp_path):
    for tk in ("31UFS", "31UFT", "31UFU"):
        _score_cog(tmp_path / f"staging-data/raster/2025/{tk}/{tk}.tif")
    (tmp_path / "one.txt").write_text("31UFT\n")
    (tmp_path / "index").mkdir()
    pq.write_table(
        pa.table({"tile_key": ["31UFT"]}), tmp_path / "index/tile_index_2025.parquet"
    )
    r = _run(tmp_path, "--tile-list", "one.txt")
    assert "1 tiles" in r.stdout, r.stdout + r.stderr


def test_outline_stamp_names_the_method_that_ran(tmp_path):
    from types import SimpleNamespace

    src = tmp_path / "tile.tif"  # unreadable on purpose: no COG tags, the spec must still stamp
    src.write_bytes(b"not a tif")
    method = SimpleNamespace(id="nbg-pb-h0.01-t0.3+R35+F10+G2+A900+q1")
    stamp = ol.outline_provenance(src, 2025, method, "fast")
    assert stamp["spec"] == method.id and stamp["backend"] == "fast"
    assert ol.method_for.__doc__ and "+q1" in ol.method_for.__doc__
