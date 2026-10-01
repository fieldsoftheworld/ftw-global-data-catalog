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


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_warp_non_404_http_errors_raise(monkeypatch, code):
    """Only a genuinely absent tile is missing coverage.

    Classifying on English error text matched 'No such file or directory', which
    GDAL uses for several unrelated refusals, so a 403 or a timeout could be
    recorded as a hole in the DEM.
    """

    def fail(*a: object, **k: object) -> None:
        raise RasterioIOError(f"HTTP response code: {code}")

    monkeypatch.setattr(tfa.rasterio, "open", fail)
    with pytest.raises(RasterioIOError, match=str(code)):
        tfa._warp(["/vsicurl/x.tif"], (2, 2), "EPSG:4326", None, Resampling.nearest, np.uint8)


def test_warp_timeout_raises(monkeypatch):
    def slow(*a: object, **k: object) -> None:
        raise RasterioIOError("CURL error: Operation timed out after 30000 milliseconds")

    monkeypatch.setattr(tfa.rasterio, "open", slow)
    with pytest.raises(RasterioIOError, match="timed out"):
        tfa._warp(["/vsis3/b/k.tif"], (2, 2), "EPSG:4326", None, Resampling.nearest, np.float32)


def test_warp_error_message_names_the_source(monkeypatch):
    def fail(*a: object, **k: object) -> None:
        raise RasterioIOError("HTTP response code: 403")

    monkeypatch.setattr(tfa.rasterio, "open", fail)
    with pytest.raises(RasterioIOError, match=r"/vsicurl/dem/N59E005.tif: "):
        tfa._warp(
            ["/vsicurl/dem/N59E005.tif"], (2, 2), "EPSG:4326", None, Resampling.nearest, np.uint8
        )


def test_absent_is_classified_on_status_not_on_prose():
    assert tfa.is_absent("/vsicurl/x.tif", RasterioIOError("HTTP response code: 404"))
    assert tfa.is_absent("/vsicurl/x.tif", RasterioIOError("HTTP response code: 410"))
    assert not tfa.is_absent("/vsicurl/x.tif", RasterioIOError("HTTP response code: 403"))
    # the allow-list rejection GDAL phrases as a filesystem miss must NOT read as absent
    allow_list_reject = RasterioIOError(
        "'/vsicurl/https://h/Nodes(B04.tif)/$value' does not exist in the file system, "
        "and is not recognized as a supported dataset name."
    )
    assert not tfa.is_absent("/vsicurl/https://h/Nodes(B04.tif)/$value", allow_list_reject)


#: PR1's CDSE source-mosaic href shape: the ``.tif`` is inside a Nodes() segment and
#: the URL ends in ``/$value``, so an extension allow-list rejects it.
CDSE_HREF = (
    "/vsicurl/https://cdse.invalid/odata/v1/Products(x)/Nodes(B04.tif)/$value"
)
_EXT_REJECT = "not recognized as a supported dataset name"


def test_vsicurl_opts_do_not_allow_list_extensions():
    assert "CPL_VSIL_CURL_ALLOWED_EXTENSIONS" not in tfa.VSICURL_OPTS
    import outlines

    # importing outlines must not leave an allow-list behind in the environment
    assert outlines.PX_M2 == 6.25
    assert "CPL_VSIL_CURL_ALLOWED_EXTENSIONS" not in os.environ


def test_cdse_href_reaches_the_transport_layer(monkeypatch):
    "A CDSE-shaped href must fail at the request, not be refused before one is made."
    monkeypatch.delenv("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", raising=False)
    with pytest.raises(RasterioIOError) as e:
        with tfa.rasterio.Env(**tfa.VSICURL_OPTS), tfa.rasterio.open(CDSE_HREF):
            pass
    assert _EXT_REJECT not in str(e.value)


def test_extension_allow_list_would_refuse_cdse_hrefs(monkeypatch):
    "Guards the regression: with the allow-list GDAL never issues a request."
    monkeypatch.setenv("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    with pytest.raises(RasterioIOError) as e:
        with tfa.rasterio.open(CDSE_HREF):
            pass
    assert _EXT_REJECT in str(e.value)


#: PR1's QA index row: the S3 object its downloader fetches, plus the endpoint the
#: row was written against. b04_odata_href is provenance only and never opened.
S3_HREF = "s3://eodata/Sentinel-2/MSI/MSI_L3__MCQ/2025/Q1/T31UFS/B04.tif"
S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"


def _index(tmp_path: Path, tile: str = "31UFS", **over) -> Path:
    idx = tmp_path / "tile_index_2025.parquet"
    cols = {
        "tile_key": [tile] * 4,
        "quarter": ["Q1", "Q2", "Q3", "Q4"],
        "b04_s3_href": [S3_HREF] * 4,
        "b04_s3_endpoint": [S3_ENDPOINT] * 4,
    }
    pq.write_table(pa.table({**cols, **over}), idx)
    return idx


def test_context_read_failure_names_tile_and_href(tmp_path, monkeypatch):
    import context

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s")
    monkeypatch.setattr(
        context, "_vsis3", lambda h, e: ("/vsis3/eodata/nope.tif", {"AWS_S3_ENDPOINT": "x.invalid"})
    )
    with pytest.raises(RasterioIOError, match=r"31UFS Q1: cannot read /vsis3/eodata/nope.tif"):
        context.aux_rasters("31UFS", 2025, _index(tmp_path), "EPSG:32631", None, (4, 4))


def test_context_uses_the_rows_s3_href_and_endpoint():
    import context

    src, opts = context._vsis3(S3_HREF, S3_ENDPOINT)
    assert src == "/vsis3/eodata/Sentinel-2/MSI/MSI_L3__MCQ/2025/Q1/T31UFS/B04.tif"
    assert opts["AWS_S3_ENDPOINT"] == S3_ENDPOINT
    assert opts["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert "CPL_VSIL_CURL_ALLOWED_EXTENSIONS" not in opts


def test_context_rejects_an_odata_href_and_a_missing_endpoint():
    import context

    with pytest.raises(ValueError, match="expected an s3:// href"):
        context._vsis3("https://catalogue.dataspace.copernicus.eu/odata/v1/x/$value", S3_ENDPOINT)
    with pytest.raises(ValueError, match="no b04_s3_endpoint"):
        context._vsis3(S3_HREF, "")


def test_missing_credentials_fail_with_a_message_naming_the_need(tmp_path, monkeypatch):
    import context

    for v in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_NO_SIGN_REQUEST"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RasterioIOError, match="needs credentials"):
        context.aux_rasters("31UFS", 2025, _index(tmp_path), "EPSG:32631", None, (4, 4))


def test_antimeridian_dem_longitudes_are_not_empty():
    """A dateline tile reports west > east, not an unwrapped span.

    Measured for a zone-1 tile: w = 178.913, e = -178.779, so e - w = -357.69 is
    not > 180 and the plain branch ran range(178, -178) -- EMPTY. No DEM tile was
    fetched at all and every parcel got a NaN elevation.
    """
    import context

    assert context.dem_lons(178.913, -178.779) == [178, 179, -180, -179]
    assert context.dem_lons(4.2, 6.8) == [4, 5, 6]
    assert context.dem_lons(-3.5, -1.2) == [-4, -3, -2]


def test_land_cover_vintages_follow_the_product_year():
    "year was accepted and ignored: the 2020 product read 2024 land cover."
    import context

    assert max(context.lulc_years(2020)) == 2020
    assert max(context.lulc_years(2024)) == 2024
    assert all(y <= 2020 for y in context.lulc_years(2020))
    with pytest.raises(ValueError, match="no land-cover vintages defined for product year 2099"):
        context.lulc_years(2099)


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


def test_simplify_refreshes_area_and_part_count(tmp_path):
    """area_m2 and n_parts must describe the geometry actually written.

    Only the bounds and the WKB used to be refreshed, so a measured row kept
    area_m2 = 1006.0 against a written geometry of 250.9 m2 -- and merge reads
    area_m2 for both its --max-km2 cap and its _summary.json totals.
    """
    import shapely
    from pyproj import Transformer

    import simplify_polygons as spm

    rng = np.random.default_rng(1)
    th = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    r = 0.00018 + rng.normal(0, 0.00004, 400)
    g = shapely.make_valid(
        shapely.Polygon([(3.0 + rr * np.cos(t), 51.0 + rr * np.sin(t) * 0.62) for t, rr in zip(th, r)])
    )
    src = tmp_path / "outlines/2025/31UFS.parquet"
    src.parent.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "tile_key": ["31UFS"],
                "area_m2": [1006.0],
                "n_parts": pa.array([1], pa.int64()),
                "xmin": [2.99],
                "ymin": [50.99],
                "xmax": [3.01],
                "ymax": [51.01],
                "geometry": [shapely.to_wkb(g)],
            }
        ),
        src,
    )
    spm.process_tile("31UFS", 2025, 5.0, tmp_path / "outlines", tmp_path / "simplified")
    out = pq.read_table(tmp_path / "simplified/2025/31UFS.parquet")
    written = shapely.from_wkb(out["geometry"].to_numpy(zero_copy_only=False))[0]
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:32631", always_xy=True)
    utm = shapely.transform(written, lambda a: np.c_[fwd.transform(a[:, 0], a[:, 1])])
    assert out["area_m2"][0].as_py() == pytest.approx(utm.area, rel=1e-6)
    assert out["area_m2"][0].as_py() != pytest.approx(1006.0, rel=1e-3)
    assert out["n_parts"][0].as_py() == shapely.get_num_geometries(written)
    # and the covering columns match the written geometry, with no NaN
    assert out["xmin"][0].as_py() == pytest.approx(written.bounds[0])
    assert not np.isnan([out[k][0].as_py() for k in ("xmin", "ymin", "xmax", "ymax")]).any()


def test_simplify_empty_table_written_atomically(tmp_path):
    src, dst = tmp_path / "in/T.parquet", tmp_path / "out/T.parquet"
    _src(src, n=0)
    dst.parent.mkdir(parents=True)
    sp.write_atomic(pq.read_table(src), dst, sp.fingerprint(src, 5.0))
    assert sp.is_current(dst, src, 5.0)
    assert not list(dst.parent.glob("*.tmp-*"))
