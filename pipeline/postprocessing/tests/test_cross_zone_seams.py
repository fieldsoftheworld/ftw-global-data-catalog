"""cross_zone_seams: claimed orphan-strip parcels absorb the next zone's cut piece."""

import json
import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest
import shapely

PP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PP))

import cross_zone_seams as cz
import merge_polygons as mp


def _con():
    con = duckdb.connect()
    con.sql("load spatial")
    return con


def _zone(root, zone, rows, claimed_col, wkt=None, fp="abc"):
    """Merged zone file from (tile_key, parcel_id, x0, y0, x1, y1, pf[, claimed]); ``wkt``
    replaces a parcel's envelope (an invalid ring, say)."""
    f = root / "2025" / f"zone={zone}" / "part-0.parquet"
    f.parent.mkdir(parents=True)
    vals = ", ".join(
        f"('{t}', {p}, {x0}::DOUBLE, {y0}::DOUBLE, {x1}::DOUBLE, {y1}::DOUBLE, {pf}::DOUBLE, "
        f"{str(bool(c[0]) if c else False).lower()}, "
        + (f"ST_GeomFromText('{wkt[p]}')" if wkt and p in wkt
           else f"ST_MakeEnvelope({x0}, {y0}, {x1}, {y1})")
        + ")"
        for t, p, x0, y0, x1, y1, pf, *c in rows
    )
    cols = "tile_key, parcel_id, xmin, ymin, xmax, ymax, pf_mean, claimed, geometry"
    extra = ", ST_Area(ST_Transform(geometry, 'EPSG:4326', 'EPSG:32632', true)) AS area_m2"
    sel = "*" if claimed_col else "* EXCLUDE (claimed)"
    _con().sql(f"COPY (SELECT {sel}{extra} FROM (VALUES {vals}) t({cols})) TO '{f}' "
               f"(FORMAT parquet, KV_METADATA {{merge_fingerprint: '{fp}'}})")


def _ids(root, zone):
    f = root / "2025" / f"zone={zone}" / "part-0.parquet"
    return [r[0] for r in _con().sql(f"select parcel_id from '{f}' order by 1").fetchall()]


def test_claimed_field_absorbs_the_next_zones_cut_piece(tmp_path):
    # NL strip at 52.7N: 31UGU claims a field from 6.020 to 6.040E; 32ULD kept its piece east of
    # its raster edge (6.030E); an unrelated 32ULD field and an unclaimed 31UGU field stay
    _zone(tmp_path, "31", [("31UGU_0_0", 7, 6.020, 52.700, 6.040, 52.705, 0.6, True),
                           ("31UGU_0_0", 8, 5.900, 52.700, 5.910, 52.705, 0.5, False)], True)
    _zone(tmp_path, "32", [("32ULD_0_0", 3, 6.030, 52.700, 6.040, 52.705, 0.8),
                           ("32ULD_0_0", 4, 6.100, 52.700, 6.110, 52.705, 0.4)], False)
    r = cz.run(tmp_path, 2025)
    assert r["zones"] == {"31": {"unions": 1, "dropped": 0, "rows": 2},
                          "32": {"unions": 0, "dropped": 1, "rows": 1}}
    f31 = tmp_path / "2025/zone=31/part-0.parquet"
    z31 = _con().sql(f"select parcel_id, xmin, xmax, pf_mean from '{f31}' order by 1").fetchall()
    assert [r[0] for r in z31] == [7, 8]
    assert (z31[0][1], z31[0][2]) == (6.02, 6.04)
    assert abs(z31[0][3] - (0.6 * 2 + 0.8) / 3) < 0.01  # area-weighted: the piece is half
    assert _ids(tmp_path, "32") == [4]
    assert json.loads((tmp_path / "2025" / "_cross_zone.json").read_text())["pairs"]
    md = pq.ParquetFile(f31).schema_arrow.metadata
    assert md[b"merge_fingerprint"] == b"abc"  # merge's resume key survives the rewrite
    assert md[cz.SEAM_KEY] == b"1"
    assert not list(tmp_path.glob("2025/zone=*/part-0.parquet.cz-tmp"))


def test_no_claimed_column_leaves_files_alone(tmp_path):
    _zone(tmp_path, "31", [("31UGU_0_0", 1, 5.9, 52.7, 5.91, 52.705, 0.5)], False)
    _zone(tmp_path, "32", [("32ULD_0_0", 1, 6.1, 52.7, 6.11, 52.705, 0.5)], False)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.glob("2025/zone=*/part-0.parquet")}
    assert cz.run(tmp_path, 2025)["zones"] == {}
    assert before == {p: p.stat().st_mtime_ns for p in before}


def test_only_the_source_zones_claims_are_paired(tmp_path):
    _zone(tmp_path, "31", [("31UGU_0_0", 7, 6.020, 52.700, 6.040, 52.705, 0.6, True)], True)
    _zone(tmp_path, "32", [("32ULD_0_0", 3, 6.030, 52.700, 6.040, 52.705, 0.8)], False)
    assert cz.run(tmp_path, 2025, sources={32})["zones"] == {}
    assert _ids(tmp_path, "32") == [3]


def test_union_area_accounts_for_overlapping_members_in_one_zone(tmp_path):
    _zone(tmp_path, "31", [("31UGU_0_0", 1, 6.020, 52.700, 6.040, 52.705, 0.6, True),
                           ("31UGU_0_0", 2, 6.020, 52.700, 6.040, 52.705, 0.6, True)], True)
    _zone(tmp_path, "32", [("32ULD_0_0", 3, 6.030, 52.700, 6.040, 52.705, 0.8)], False)
    cz.run(tmp_path, 2025)
    rows = _con().sql(
        "SELECT parcel_id, area_m2, ST_Area(ST_Transform(ST_MakeEnvelope(6.020, 52.700, 6.040, "
        f"52.705), 'EPSG:4326', 'EPSG:32631', true)) FROM '{tmp_path}/2025/zone=31/part-0.parquet'"
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == 1
    assert rows[0][1] == pytest.approx(rows[0][2], rel=1e-6)


def test_mutual_claims_merge_once_into_the_lower_zone(tmp_path):
    _zone(tmp_path, "31", [("31UGU_0_0", 7, 6.020, 52.700, 6.040, 52.705, 0.6, True)], True)
    _zone(tmp_path, "32", [("32ULD_0_0", 3, 6.030, 52.700, 6.045, 52.705, 0.8, True)], True)
    cz.run(tmp_path, 2025)
    assert (_ids(tmp_path, "31"), _ids(tmp_path, "32")) == ([7], [])


def test_polygon_union_flattens_nested_collections():
    a, b = shapely.box(0, 0, 1, 1), shapely.box(2, 0, 3, 1)
    g = shapely.GeometryCollection([shapely.MultiPolygon([a, b]),
                                    shapely.LineString([(0, 0), (5, 5)])])
    assert cz.polygon_union(g).area == 2.0


def test_invalid_ring_in_a_neighbour_piece_is_repaired_before_the_overlay(tmp_path):
    # an unrepaired coverage-simplify ring (a hole crossing its shell) made GEOS raise in the
    # 2025 r1 run; the piece is made valid, paired and absorbed
    bad = ("POLYGON((6.030 52.700, 6.040 52.700, 6.040 52.705, 6.030 52.705, 6.030 52.700),"
           "(6.035 52.702, 6.045 52.702, 6.045 52.703, 6.035 52.703, 6.035 52.702))")
    _zone(tmp_path, "31", [("31UGU_0_0", 7, 6.020, 52.700, 6.040, 52.705, 0.6, True)], True)
    _zone(tmp_path, "32", [("32ULD_0_0", 3, 6.030, 52.700, 6.040, 52.705, 0.8)], False, {3: bad})
    r = cz.run(tmp_path, 2025)
    assert r["zones"] == {"31": {"unions": 1, "dropped": 0, "rows": 1},
                          "32": {"unions": 0, "dropped": 1, "rows": 0}}
    ok, xmax = _con().sql(
        f"select ST_IsValid(geometry), xmax from '{tmp_path}/2025/zone=31/part-0.parquet'"
    ).fetchall()[0]
    assert ok
    assert abs(xmax - 6.045) < 1e-9  # make_valid keeps the hole's part outside the shell


def _tile(root, tk, rows, claimed):
    """Simplified-tile file; ``claimed=None`` writes the schema from before the claim rule."""
    vals = ", ".join(
        f"('{tk}', {p}, {x0}::DOUBLE, {y0}::DOUBLE, {x1}::DOUBLE, {y1}::DOUBLE, 0.5::DOUBLE, "
        f"true, true, 0.0::DOUBLE, ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}))"
        for p, x0, y0, x1, y1 in rows
    )
    cols = ("tile_key, parcel_id, xmin, ymin, xmax, ymax, pf_mean, in_utm_zone, in_mgrs_square, "
            "frac_water, geometry")
    area = ", ST_Area(ST_Transform(geometry, 'EPSG:4326', 'EPSG:32632', true)) AS area_m2"
    sel = f"SELECT *{area} FROM (VALUES {vals}) t({cols})"
    if claimed is not None:
        flags = ", ".join(f"({p}, {str(c).lower()})"
                          for (p, *_), c in zip(rows, claimed, strict=True))
        sel = (f"SELECT s.*, c.claimed FROM ({sel}) s "
               f"JOIN (VALUES {flags}) c(pid, claimed) ON pid = parcel_id")
    (root / "2025").mkdir(parents=True, exist_ok=True)
    _con().sql(f"COPY ({sel}) TO '{root / '2025' / f'{tk}.parquet'}' (FORMAT parquet)")


def _merge(tmp_path, monkeypatch, *extra):
    keep = tmp_path / "keep.txt"
    keep.write_text("31AAA_0_0\n31UGU_0_0\n32ULD_0_0\n")
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    argv = ["merge_polygons.py", "--year", "2025", "--in-root", str(tmp_path / "simplified"),
            "--out-root", str(tmp_path / "merged"), "--tmp-dir", str(tmp_path / "spill"),
            "--no-aux", "--keep-list", str(keep), "--empty-list", str(empty),
            "--threads", "1", "--memory", "1GB", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    mp.main()


def test_merge_keeps_claimed_when_the_first_file_lacks_it(tmp_path, monkeypatch):
    simp, merged = tmp_path / "simplified", tmp_path / "merged"
    # 31AAA sorts first and has no `claimed` column; 31UGU claims a field across 6.03E
    _tile(simp, "31AAA_0_0", [(1, 5.0, 52.70, 5.01, 52.705)], None)
    _tile(simp, "31UGU_0_0", [(7, 6.020, 52.700, 6.040, 52.705)], [True])
    _tile(simp, "32ULD_0_0", [(3, 6.030, 52.700, 6.040, 52.705)], [False])
    _merge(tmp_path, monkeypatch)
    z31 = _con().sql(
        f"select parcel_id, claimed from '{merged}/2025/zone=31/part-0.parquet' order by 1"
    ).fetchall()
    assert z31 == [(1, None), (7, True)]
    assert _ids(merged, "32") == []  # the cut piece joined the claimed field in zone 31
    summary = json.loads((merged / "2025" / "_summary.json").read_text())
    assert summary["cross_zone"]["pairs"] == 1
    # a full resume skips every zone and leaves the seamed files as they are
    _merge(tmp_path, monkeypatch)
    assert _ids(merged, "31") == [1, 7] and _ids(merged, "32") == []


def test_a_resumed_zone_next_to_a_rewritten_one_needs_force(tmp_path, monkeypatch):
    simp = tmp_path / "simplified"
    _tile(simp, "31AAA_0_0", [(1, 5.0, 52.70, 5.01, 52.705)], None)
    _tile(simp, "31UGU_0_0", [(7, 6.020, 52.700, 6.040, 52.705)], [True])
    _tile(simp, "32ULD_0_0", [(3, 6.030, 52.700, 6.040, 52.705), (4, 6.1, 52.7, 6.11, 52.705)],
          [False, False])
    _merge(tmp_path, monkeypatch)
    _tile(simp, "32ULD_0_0", [(4, 6.1, 52.7, 6.11, 52.705)], [False])  # rewritten input
    with pytest.raises(SystemExit, match="--force"):
        _merge(tmp_path, monkeypatch)
    _merge(tmp_path, monkeypatch, "--force")
