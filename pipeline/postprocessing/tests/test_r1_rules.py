"""The rules the published 2017-2025 vectors were made with: BoundaryVote spec, inland water, sea."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pyproj import Transformer

PP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PP))

import context
import outlines as ol
import sea_filter as sf
from test_fiboa_convert import _write_zone, fc

MP = importlib.util.spec_from_file_location("merge_polygons", PP / "merge_polygons.py")
mp = importlib.util.module_from_spec(MP)
MP.loader.exec_module(mp)


def test_boundaryvote_spec_is_the_published_one():
    assert ol.SPEC == "nbg-pb-h0.01-t0.5+R25+F10+G2+A900"
    assert ol.SPEC + "+q1" == "nbg-pb-h0.01-t0.5+R25+F10+G2+A900+q1"
    assert subprocess.run(
        [sys.executable, str(PP / "outlines.py"), "--help"], capture_output=True, text=True
    ).stdout.count("fast (default") == 1


def test_the_spec_is_part_of_the_outline_fingerprint(tmp_path, monkeypatch):
    src = tmp_path / "score.tif"
    src.write_bytes(b"x")
    before = ol.fingerprint(src, 2025, 8192, 512, "fast", 0.0)
    monkeypatch.setattr(ol, "SPEC", ol.SPEC.replace("t0.5", "t0.3"))
    assert ol.fingerprint(src, 2025, 8192, 512, "fast", 0.0) != before


def test_every_year_has_land_cover_vintages_that_never_pass_the_product_year():
    for year in range(2017, 2026):
        vintages = context.lulc_years(year)
        assert vintages and max(vintages) <= year
        assert max(vintages) <= context.WATER_VINTAGE
    assert context.WATER_VINTAGE == 2024


def test_the_water_mask_uses_one_vintage_for_every_year(monkeypatch):
    """frac_water is io-lulc 2024 for 2017 as for 2025; frac_crops_ever follows the year."""
    asked = []

    def warp(srcs, shape, crs, transform, resampling, dtype):
        out = np.zeros(shape, dtype)
        if dtype == np.uint8:
            year = int(srcs[0].rsplit("_", 1)[1].split(".")[0])
            asked.append(year)
            out[:] = context.WATER if year == 2024 else context.CROP if year == 2017 else 0
        return out

    monkeypatch.setattr(context, "_warp", warp)
    monkeypatch.setattr(context, "_require_credentials", lambda: None)
    monkeypatch.setattr(context, "nodata_cells", lambda ds, h, w: np.zeros((h, w), bool))
    index = {"quarter": [f"Q{i}" for i in range(1, 5)], "b04_s3_href": ["s3://b/k"] * 4,
             "b04_s3_endpoint": ["http://e"] * 4}

    class Ds:
        nodata = -32768

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(context.pq, "read_table", lambda *a, **k: pa.table(index))
    monkeypatch.setattr(context.rasterio, "open", lambda *a, **k: Ds())
    import rasterio

    tr10 = rasterio.Affine(10, 0, 500000, 0, -10, 5400000)
    got = context.aux_rasters("31UFS_0_0", 2017, Path("x"), "EPSG:32631", tr10 * tr10.scale(4), (48, 48))
    assert sorted(set(asked)) == [2017, 2024]
    assert got["water"].all()  # 2024 is water
    assert got["crops"].all()  # 2017 is cropland, and 2017 is the only vintage sampled for crops


def _tile(path: Path, tk: str, water: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ", ".join(f"({i + 1}, {w if w is not None else 'NULL'})" for i, w in enumerate(water))
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"""COPY (SELECT '{tk}' AS tile_key, parcel_id, true AS in_utm_zone,
          true AS in_mgrs_square, 1000.0 AS area_m2, 0.7 AS pf_mean, frac_water::DOUBLE AS frac_water,
          ST_MakeEnvelope(3.0 + parcel_id*0.001, 51.0, 3.0 + parcel_id*0.001 + 0.0005, 51.0005)
            AS geometry
        FROM (VALUES {rows}) t(parcel_id, frac_water)) TO '{path}' (FORMAT parquet)"""
    )
    con.close()


def test_merge_drops_parcels_at_least_70_percent_water(tmp_path):
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", [0.0, 0.69, 0.7, 0.95, None])
    (tmp_path / "tiles.txt").write_text("31UFS\n")
    (tmp_path / "empty.txt").write_text("")
    cmd = [
        sys.executable, str(PP / "merge_polygons.py"), "--year", "2025", "--no-aux",
        "--keep-list", "tiles.txt", "--empty-list", "empty.txt",
    ]  # fmt: skip
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "merged/2025/zone=31/part-0.parquet"
    kept = sorted(pq.read_table(out)["parcel_id"].to_pylist())
    assert kept == [1, 2, 5]  # 0.0, 0.69 and the NULL (no land-cover coverage = dry)
    summary = json.loads((tmp_path / "merged/2025/_summary.json").read_text())
    assert summary["max_frac_water"] == 0.7
    assert "frac_water" in summary["filter"]
    # --max-frac-water 2 keeps every parcel, and a different filter rewrites the zone
    r = subprocess.run([*cmd, "--max-frac-water", "2"], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(pq.read_table(out)) == 5


def test_keep_filter_is_what_the_summary_records():
    assert mp.keep_filter(5.0) == (
        "in_utm_zone AND in_mgrs_square AND area_m2 <= 5000000.0 AND coalesce(frac_water, 0) < 0.7"
    )


def _square(lon: float, lat: float, side_m: float = 200.0, epsg: int = 32631) -> shapely.Polygon:
    """Square of ``side_m`` centred on lon/lat, built in UTM, returned in lon/lat."""
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    back = Transformer.from_crs(epsg, 4326, always_xy=True)
    x, y = fwd.transform(lon, lat)
    h = side_m / 2
    pts = [(x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h)]
    return shapely.Polygon([back.transform(*p) for p in pts])


# land: lon 2-3, lat 50-51, split at lon 2.5 into two pieces overlapping by 0.001 deg (as OSM does)
LAND = np.array([shapely.box(2.0, 50.0, 2.501, 51.0), shapely.box(2.499, 50.0, 3.0, 51.0)])


def test_land_fraction_inside_outside_coast_and_split_line():
    geoms = np.array(
        [
            _square(2.2, 50.5),  # inland
            _square(1.5, 50.5),  # sea
            _square(3.0, 50.5),  # centred on the coast at 3E: half on land
            _square(2.5, 50.5),  # across the split line (and the overlap strip)
            _square(3.0, 50.5, side_m=200) - _square(3.0, 50.5, side_m=10),  # holed, coastal
        ]
    )
    frac = sf.land_fraction(geoms, LAND, shapely.STRtree(LAND), 31)
    assert frac[0] == 1.0
    assert frac[1] == 0.0
    assert frac[2] == pytest.approx(0.5, abs=0.01)
    assert frac[3] == pytest.approx(1.0, abs=1e-6)  # the overlap is not counted twice
    assert frac[4] == pytest.approx(0.5, abs=0.01)


def _land_parquet(tmp_path: Path, geoms) -> Path:
    import pyogrio.raw

    shp = tmp_path / "land_polygons.shp"
    pyogrio.raw.write(
        shp, geometry=shapely.to_wkb(np.array(geoms)), field_data=[], fields=[],
        crs="EPSG:4326", geometry_type="Polygon", driver="ESRI Shapefile",
    )  # fmt: skip
    dst = tmp_path / "land.parquet"
    assert sf.prep_land_polygons(shp, dst) == len(geoms)
    return dst


def test_prepared_land_polygons_carry_bbox_columns_sorted_by_xmin(tmp_path):
    dst = _land_parquet(tmp_path, [shapely.box(10, 10, 11, 11), shapely.box(-3.1, 40.9, -2.99, 41.5)])
    t = pq.read_table(dst)
    assert t.column_names == ["geometry", "xmin", "ymin", "xmax", "ymax"]
    assert t["xmin"].to_pylist() == [-3.1, 10.0]


def test_sea_filter_drops_rows_below_half_land(tmp_path):
    dst = _land_parquet(tmp_path, [shapely.box(2.0, 50.0, 3.0, 51.0)])
    geoms = [_square(2.2, 50.5), _square(1.5, 50.5), _square(2.9985, 50.5, 400.0)]  # ~75% land
    b = shapely.bounds(np.array(geoms))
    batch = pa.record_batch(
        {
            "id": pa.array(["land", "sea", "coast"]),
            "bbox": pa.StructArray.from_arrays(
                [pa.array(b[:, i]) for i in range(4)], ["xmin", "ymin", "xmax", "ymax"]
            ),
            "geometry": pa.array(shapely.to_wkb(np.array(geoms)), pa.binary()),
        }
    )
    sea = sf.SeaFilter(dst, 31)
    out = list(sea([batch]))
    assert sea.dropped == 1
    assert out[0].column("id").to_pylist() == ["land", "coast"]


def _convert(tmp_path, monkeypatch, land=None, summary=None):
    n = _write_zone(tmp_path / "merged", 10, 3)  # 30 boxes, lon -3.0 .. -2.982, lat 41.0 ..
    if summary is not None:
        (tmp_path / "merged" / "2025" / "_summary.json").write_text(json.dumps(summary))
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    return n, fc.convert(2025, "30", 1, "1GB", tmp_path / "out", None, land)


def _details(path: Path) -> str:
    return json.loads(pq.read_schema(path).metadata[b"collection"])["determination:details"]


def test_fiboa_sea_rule_drops_offshore_parcels_and_says_so(tmp_path, monkeypatch):
    land = _land_parquet(tmp_path, [shapely.box(-3.1, 40.9, -2.99, 41.5)])  # columns 0-4 only
    n, dst = _convert(tmp_path, monkeypatch, land, {"max_km2": 5.0, "max_frac_water": 0.7})
    assert n == 30
    assert pq.ParquetFile(dst).metadata.num_rows == 15
    d = _details(dst)
    assert "parcels at least 70% Impact Observatory io-lulc 2024 water" in d
    assert "less than 50% of their area on OpenStreetMap land polygons (sea) removed" in d
    assert "no land-cover masking" not in d


def test_fiboa_without_the_rules_keeps_the_old_statement(tmp_path, monkeypatch):
    n, dst = _convert(tmp_path, monkeypatch, None, {"max_km2": 5.0})
    assert pq.ParquetFile(dst).metadata.num_rows == n
    d = _details(dst)
    assert "no land-cover masking was applied" in d
    assert "water" not in d and "sea" not in d


def test_fiboa_cli_needs_a_sea_decision(tmp_path):
    _write_zone(tmp_path / "merged", 2, 2)
    r = subprocess.run(
        [sys.executable, str(PP / "fiboa_convert.py"), "--year", "2025", "--zone", "30"],
        capture_output=True, text=True, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
    )  # fmt: skip
    assert r.returncode == 2
    assert "--land-polygons" in r.stderr and "--no-sea-filter" in r.stderr
