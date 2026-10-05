import importlib.util
import json
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

sys.path.insert(0, str(Path(__file__).parents[1]))

SPEC = importlib.util.spec_from_file_location(
    "fiboa_convert", Path(__file__).parents[1] / "fiboa_convert.py"
)
fc = importlib.util.module_from_spec(SPEC)
sys.modules["fiboa_convert"] = fc
SPEC.loader.exec_module(fc)


def _write_zone(root: Path, n_lon: int, n_lat: int) -> int:
    """n_lon x n_lat boxes (0.001 deg on a 0.002 deg pitch) in UTM zone 30 (tile 30TXM)."""
    src = root / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"""COPY (
          SELECT '30TXM_0_0' AS tile_key, (i * {n_lat} + j)::BIGINT AS parcel_id,
            (-3.0 + i * 0.002) AS xmin, (41.0 + j * 0.002) AS ymin,
            (-3.0 + i * 0.002 + 0.001) AS xmax, (41.0 + j * 0.002 + 0.001) AS ymax,
            0.75 AS pf_mean, 0.1 AS pb_mean, 0.3 AS frac_nodata_1q, false AS touches_window_edge,
            ST_MakeEnvelope(-3.0 + i * 0.002, 41.0 + j * 0.002,
                            -3.0 + i * 0.002 + 0.001, 41.0 + j * 0.002 + 0.001) AS geometry
          FROM range({n_lon}) t1(i), range({n_lat}) t2(j)
        ) TO '{src}' (FORMAT parquet)"""
    )
    return n_lon * n_lat


def test_v2_layout(tmp_path, monkeypatch):
    n = _write_zone(tmp_path / "merged", 400, 20)  # 0.8 x 0.04 deg: 20:1 zone
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    monkeypatch.setattr(fc, "ROW_GROUP", 500)
    dst = fc.convert(2025, "30", 1, "1GB", tmp_path / "out")
    pf = pq.ParquetFile(dst)
    assert pf.metadata.num_rows == n
    assert pf.schema_arrow.names == [
        "id",
        "collection",
        "geometry",
        "bbox",
        "metrics:area",
        "metrics:perimeter",
        "score",
        "determination:datetime",
        "determination:method",
    ]
    assert pf.schema_arrow.field("score").type == pa.uint8()
    assert pf.schema_arrow.field("determination:datetime").type == pa.timestamp("ms", tz="UTC")
    row = duckdb.sql(
        f'select distinct score, "determination:method", epoch("determination:datetime") '
        f"from '{dst}'"
    ).fetchall()
    assert row == [(75, "auto-imagery", 1735689600.0)]
    sizes = [pf.metadata.row_group(i).num_rows for i in range(pf.metadata.num_row_groups)]
    assert set(sizes[:-1]) == {500}
    assert sum(sizes) == n
    meta = json.loads(pf.schema_arrow.metadata[b"collection"])
    assert list(meta["schemas:custom"]["properties"]) == ["score"]
    # Hilbert cells are square in degrees even for a 20:1 zone: row groups stay compact
    # (unscaled bounds would give 0.2 x 0.01 deg groups)
    ws, hs = [], []
    for i in range(pf.metadata.num_row_groups):
        rg = pf.metadata.row_group(i)
        idx = {rg.column(j).path_in_schema: j for j in range(rg.num_columns)}
        st = {k: rg.column(idx[f"bbox.{k}"]).statistics for k in ("xmin", "xmax", "ymin", "ymax")}
        ws.append(st["xmax"].max - st["xmin"].min)
        hs.append(st["ymax"].max - st["ymin"].min)
    assert max(ws) < 0.12
    assert max(hs) <= 0.04
    tmp = tmp_path / "duck"
    assert not tmp.exists() or not any(tmp.iterdir())


BOX_COLS = "t(tile_key, parcel_id, xmin, ymin, xmax, ymax, pf_mean, touches_window_edge, geometry)"


def _write_boxes(root: Path, boxes: list[tuple]) -> Path:
    "boxes: (tile_key, parcel_id, x0, y0, x1, y1, pf_mean, touches_window_edge)"
    src = root / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    values = ", ".join(
        f"('{t}', {p}, {x0}::DOUBLE, {y0}::DOUBLE, {x1}::DOUBLE, {y1}::DOUBLE, {pf}::DOUBLE, "
        f"{str(e).lower()}, ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}))"
        for t, p, x0, y0, x1, y1, pf, e in boxes
    )
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(f"COPY (SELECT * FROM (VALUES {values}) {BOX_COLS}) TO '{src}' (FORMAT parquet)")
    con.close()
    return src


def _rows(dst: Path) -> list[tuple]:
    con = duckdb.connect()
    con.sql("load spatial")
    out = con.sql(
        f'select id, "metrics:area", bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax, '
        f"ST_XMin(geometry), ST_YMin(geometry), ST_XMax(geometry), ST_YMax(geometry) "
        f"from '{dst}' order by id"
    ).fetchall()
    con.close()
    return out


def _convert(tmp_path, monkeypatch, boxes) -> list[tuple]:
    _write_boxes(tmp_path / "merged", boxes)
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    return _rows(fc.convert(2025, "30", 1, "1GB", tmp_path / "out"))


def _seam_pair(x0: float, y0: float, w: float, h: float, a: str, b: str) -> list[tuple]:
    "One field cut by the a/b tile seam into two halves overlapping by w/2."
    return [
        (f"{a}_0_0", 1, x0, y0, x0 + w, y0 + h, 0.6, True),
        (f"{b}_0_0", 1, x0 + w / 2, y0, x0 + 1.5 * w, y0 + h, 0.8, True),
    ]


def test_seam_adjacent_fields_are_not_merged(tmp_path, monkeypatch):
    """Two distinct fields touching along a tile seam are neighbours, not duplicates.

    The cross-tile branch accepted a bare 20 m shared edge with zero overlap, so
    neighbours either side of a seam were fused with a blended score. Seams run
    for tens of thousands of km.
    """
    w, h = 0.001, 0.0005
    rows = _convert(
        tmp_path,
        monkeypatch,
        [
            ("30TXM_0_0", 1, -3.0 - w, 41.0, -3.0, 41.0 + h, 0.6, False),
            ("30TYM_0_0", 1, -3.0, 41.0, -3.0 + w, 41.0 + h, 0.8, False),
        ],
    )
    assert [r[0] for r in rows] == ["30TXM_0_0-1", "30TYM_0_0-1"]
    assert rows[0][1] == rows[1][1], "each keeps its own area; neither is the union"


def test_genuine_seam_duplicate_still_merges(tmp_path, monkeypatch):
    "Control for the above: a real duplicate overlaps, and must still become one parcel."
    rows = _convert(tmp_path, monkeypatch, _seam_pair(-3.0, 41.0, 0.001, 0.0005, "30TXM", "30TYM"))
    assert [r[0] for r in rows] == ["30TXM_0_0-1"]
    assert rows[0][1] > 6000, rows[0][1]  # the union of two ~4,668 m2 halves


#: One field cut by a same-zone tile seam at lon -3.0, each half clipped at its own raster
#: edge so the two share only the rasters' ~120 m overlap band (-3.0007 .. -2.9993).
#: `half` is the field's reach each side of the seam, in degrees; 0.0012 deg ~ 100 m at 41N.
def _clipped_seam_pair(half: float) -> list[tuple]:
    o, h = 0.0007, 0.0045
    return [
        ("30TXM_0_0", 1, -3.0 - half, 41.0, -3.0 + o, 41.0 + h, 0.6, False),
        ("30TYM_0_0", 1, -3.0 - o, 41.0, -3.0 + half, 41.0 + h, 0.8, False),
    ]


def test_a_narrow_field_across_a_same_zone_seam_is_rejoined(tmp_path, monkeypatch):
    "The abutting-squares case the seam union does handle: both halves are one field."
    rows = _convert(tmp_path, monkeypatch, _clipped_seam_pair(0.005))  # ~420 m each side
    assert [r[0] for r in rows] == ["30TXM_0_0-1"], "one field, published once"


def test_a_wide_field_across_a_same_zone_seam_is_not_rejoined(tmp_path, monkeypatch):
    """Recorded loss: MIN_OVERLAP's 10% rule cannot rejoin a wide seam-crossing field.

    Since outlines' MGRS square became the raster's own 100 km cell, both halves of
    a field straddling a same-zone tile seam are owned and published (the old 90.08 km
    square dropped them with the 5 km frame, so nothing downstream had ever had to
    handle this). Rejoining them is now the seam union's job, but same-zone rasters
    overlap by only 40-120 m, so the shared area is a fixed ~120 m band while the
    smaller half grows with the field: past roughly 1.2 km each side of the seam the
    band is under WINDOW_MIN_OVERLAP_FRAC of it and the pair is never grouped, so the
    field is published as two overlapping clipped halves.

    This test records the limit rather than blessing it: loosening the cross-tile
    branch for a shared tile boundary is the fix, and it must land before the next
    generation runs. Keep the narrow control above passing when it does.
    """
    rows = _convert(tmp_path, monkeypatch, _clipped_seam_pair(0.024))  # ~2 km each side
    assert [r[0] for r in rows] == ["30TXM_0_0-1", "30TYM_0_0-1"], "known: two halves"
    assert min(r[1] for r in rows) > 900, "both clear the floor, so neither is dropped"
    assert rows[0][8] > rows[1][6], "and they genuinely overlap: a duplicated strip"


def test_minimum_area_applies_after_the_seam_union(tmp_path, monkeypatch):
    """A field cut into two sub-900 m2 halves must publish its ~1,300 m2 union.

    The 900 m2 minimum used to run inside repaired(), which builds ``cand``, so
    both halves were deleted before ST_Union_Agg ever saw them and the field
    vanished from the release entirely.
    """
    rows = _convert(tmp_path, monkeypatch, _seam_pair(-3.0, 41.0, 0.0003, 0.0003, "30TXM", "30TYM"))
    assert [r[0] for r in rows] == ["30TXM_0_0-1"]
    assert 1200 < rows[0][1] < 1400  # the union, which clears 900 m2
    assert abs(rows[0][4] - (-3.0 + 1.5 * 0.0003)) < 1e-9  # spans both halves


def test_minimum_area_still_drops_a_genuinely_tiny_parcel(tmp_path, monkeypatch):
    "Control: the minimum must still apply -- to the final geometry."
    rows = _convert(
        tmp_path,
        monkeypatch,
        [("30TXM_0_0", 1, -3.0, 41.0, -3.0 + 0.0002, 41.0 + 0.0002, 0.6, False)],
    )
    assert rows == []


def test_cross_equator_seam_join_fires(tmp_path, monkeypatch):
    """Halves owned by a band-M and a band-N tile belong to one field.

    Per-row hemisphere EPSG put them 10,000 km apart (northing 55.3 in 32630 vs
    10,000,055.3 in 32730), so ST_Intersects never matched and both halves were
    published as separate parcels.
    """
    boxes = _seam_pair(-3.0, 0.0, 0.002, 0.002, "30MXX", "30NXX")
    rows = _convert(tmp_path, monkeypatch, boxes)
    assert [r[0] for r in rows] == ["30MXX_0_0-1"], "equator halves must merge into one parcel"
    # identical geometry with both tiles in band N (the control that always worked)
    both_north = _convert(
        tmp_path / "n", monkeypatch, _seam_pair(-3.0, 0.0, 0.002, 0.002, "30NWX", "30NXX")
    )
    assert abs(rows[0][1] - both_north[0][1]) < 1.0, "same field, same area either side"


def test_max_area_cap_applies_after_the_seam_union(tmp_path, monkeypatch):
    """merge's --max-km2 is a pre-union, pre-simplification pixel-area cap.

    Two halves each under 5 km2 unioned to 6.4 km2 and were published while
    determination:details claimed 'parcels > 5 km2 removed'.
    """
    boxes = _seam_pair(-3.0, 41.0, 0.0255, 0.0180, "30TXM", "30TYM")
    rows = _convert(tmp_path, monkeypatch, boxes)
    assert all(r[1] <= fc.MAX_PARCEL_M2 for r in rows), [r[1] for r in rows]
    assert rows == [], "a 6.4 km2 union is over the 5 km2 cap and must not be published"


def test_bbox_describes_the_published_geometry(tmp_path, monkeypatch):
    """The bbox struct is the covering readers prune on; it must match geometry.

    It used to be copied from the source row while ``g`` had been through
    ST_MakeValid and per-part 900 m2 filtering, publishing a bbox ~4 km wider
    than the geometry.
    """
    src = tmp_path / "merged" / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"""COPY (SELECT '30TXM_0_0' AS tile_key, 1::BIGINT AS parcel_id,
          -3.0 AS xmin, 41.0 AS ymin, -2.95 AS xmax, 41.001 AS ymax,
          0.6 AS pf_mean, false AS touches_window_edge,
          ST_Collect([ST_MakeEnvelope(-3.0, 41.0, -2.999, 41.001),
                      ST_MakeEnvelope(-2.9501, 41.0, -2.95, 41.00005)]) AS geometry)
        TO '{src}' (FORMAT parquet)"""
    )
    con.close()
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    rows = _rows(fc.convert(2025, "30", 1, "1GB", tmp_path / "out"))
    assert len(rows) == 1
    _, _, bx0, by0, bx1, by1, gx0, gy0, gx1, gy1 = rows[0]
    assert (bx0, by0, bx1, by1) == (gx0, gy0, gx1, gy1)
    assert abs(bx1 - (-2.999)) < 1e-9, "the dropped sub-900 m2 part must not widen the bbox"


def test_duplicate_parcel_ids_are_refused_before_publication(tmp_path, monkeypatch):
    """write_sorted's validate hook was never passed and n was only printed.

    catalog/vector/{year}/AGENTS.md promises parcel ids are unique within a zone
    file, so a duplicated (tile_key, parcel_id) must not reach the release.
    """
    boxes = [  # same key twice, far apart, so no seam join merges them
        ("30TXM_0_0", 1, -3.0, 41.0, -3.0 + 0.001, 41.001, 0.6, False),
        ("30TXM_0_0", 1, -2.5, 41.0, -2.5 + 0.001, 41.001, 0.8, False),
    ]
    with pytest.raises(SystemExit, match="duplicate parcel ids"):
        _convert(tmp_path, monkeypatch, boxes)
    # and nothing was published
    assert not list((tmp_path / "out").rglob("*.parquet"))


def test_output_is_hive_partitioned_by_zone(tmp_path, monkeypatch):
    """The published layout is ``vector/{year}/zone=NN/utm{NN}.parquet``.

    ``catalog/vector/AGENTS.md`` documents it and every collection declares
    ``"partition:glob": "./zone=*/utm*.parquet"``; a flat ``utm{NN}.parquet``
    makes that glob resolve to nothing and every item data href 404.
    """
    _write_zone(tmp_path / "merged", 2, 2)
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    out = tmp_path / "out"
    dst = fc.convert(2025, "30", 1, "1GB", out)
    assert dst == out / "2025" / "zone=30" / "utm30.parquet"
    assert dst.is_file()
    assert sorted(p.name for p in (out / "2025").iterdir()) == ["zone=30"]
    # the catalog's own partition glob must find it
    assert [p.relative_to(out / "2025") for p in (out / "2025").glob("zone=*/utm*.parquet")] == [
        Path("zone=30/utm30.parquet")
    ]
    # and hive_partitioning recovers the zone column DuckDB readers rely on
    assert duckdb.sql(
        f"select distinct zone::VARCHAR from read_parquet('{out}/2025/zone=*/utm*.parquet', "
        "hive_partitioning=1)"
    ).fetchall() == [("30",)]
    # tools/rebuild_index.py rejects any href without /zone= ("some hrefs did not
    # rewrite"), so a flat layout also breaks the repo's own index rebuild
    assert "/zone=" in dst.relative_to(out).as_posix()


def test_cross_tile_seam_join(tmp_path, monkeypatch):
    # A1 and B1 overlap across a tile join (a field cut at both rasters' edges) and merge;
    # A2 only shares an edge with A1 inside the same tile (real neighbours) and stays separate.
    src = tmp_path / "merged" / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    boxes = [  # tile_key, parcel_id, x0, y0, x1, y1, pf_mean
        ("30TXM_0_0", 1, -3.0010, 41.000, -3.0000, 41.001, 0.6),
        ("30TXM_0_0", 2, -3.0010, 41.001, -3.0000, 41.002, 0.5),
        ("30TYM_0_0", 1, -3.0002, 41.000, -2.9992, 41.001, 0.8),
        ("30TYM_0_0", 2, -2.9800, 41.000, -2.9790, 41.001, 0.4),
    ]
    values = ", ".join(
        f"('{t}', {p}, {x0}::DOUBLE, {y0}::DOUBLE, {x1}::DOUBLE, {y1}::DOUBLE, {pf}::DOUBLE, "
        f"ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}))"
        for t, p, x0, y0, x1, y1, pf in boxes
    )
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"COPY (SELECT *, false AS touches_window_edge FROM (VALUES {values}) "
        f"t(tile_key, parcel_id, xmin, ymin, xmax, ymax, pf_mean, geometry)) "
        f"TO '{src}' (FORMAT parquet)"
    )
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    dst = fc.convert(2025, "30", 1, "1GB", tmp_path / "out")
    rows = duckdb.sql(
        f"select id, score, bbox.xmin, bbox.xmax, \"metrics:area\" from '{dst}' order by id"
    ).fetchall()
    assert [r[0] for r in rows] == ["30TXM_0_0-1", "30TXM_0_0-2", "30TYM_0_0-2"]
    merged = rows[0]
    assert merged[1] == 70  # area-weighted mean of 0.6 and 0.8
    assert abs(merged[2] - -3.0010) < 1e-9
    assert abs(merged[3] - -2.9992) < 1e-9
    assert merged[4] > 1.5 * rows[1][4]  # union of the two halves, not one of them


def test_zone_index_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(sys, "argv", ["fiboa_convert.py", "--year", "2025", "--zone-index", "0"])
    with pytest.raises(SystemExit, match="no zone"):
        fc.main()
    _write_zone(tmp_path / "merged", 2, 2)
    for bad in ("-1", "1"):
        monkeypatch.setattr(
            sys, "argv", ["fiboa_convert.py", "--year", "2025", "--zone-index", bad]
        )
        with pytest.raises(SystemExit, match="outside 0-0"):
            fc.main()


def test_window_seam_duplicates_join(tmp_path, monkeypatch):
    # W1/W2 (and 5/6, where only one copy is truncated): one field seen by two windows of the same tile (both touch a window edge,
    # overlap ~60%) -> one parcel. N1 also touches a window edge but only overlaps W2 by a sliver
    # (a real neighbour from the other window) and stays separate, as does interior parcel I1.
    src = tmp_path / "merged" / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    boxes = [  # tile_key, parcel_id, x0, y0, x1, y1, pf_mean, touches_window_edge
        ("30TXM_0_0", 1, -3.0010, 41.0000, -3.0000, 41.0010, 0.6, True),
        ("30TXM_0_0", 2, -3.0010, 41.0004, -3.0000, 41.0014, 0.8, True),
        ("30TXM_0_0", 3, -3.0000 - 0.00001, 41.0000, -2.9990, 41.0010, 0.5, True),
        ("30TXM_0_0", 4, -2.9900, 41.0000, -2.9890, 41.0010, 0.4, False),
        # truncated copy (touches the edge) inside the other window's complete copy -> one parcel
        ("30TXM_0_0", 5, -2.9800, 41.0000, -2.9790, 41.0010, 0.4, False),
        ("30TXM_0_0", 6, -2.9800, 41.0000, -2.9790, 41.0005, 0.4, True),
    ]
    values = ", ".join(
        f"('{t}', {p}, {x0}::DOUBLE, {y0}::DOUBLE, {x1}::DOUBLE, {y1}::DOUBLE, {pf}::DOUBLE, "
        f"{str(e).lower()}, ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}))"
        for t, p, x0, y0, x1, y1, pf, e in boxes
    )
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"COPY (SELECT * FROM (VALUES {values}) "
        f"t(tile_key, parcel_id, xmin, ymin, xmax, ymax, pf_mean, touches_window_edge, geometry)) "
        f"TO '{src}' (FORMAT parquet)"
    )
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    dst = fc.convert(2025, "30", 1, "1GB", tmp_path / "out")
    rows = duckdb.sql(f"select id, score, bbox.ymin, bbox.ymax from '{dst}' order by id").fetchall()
    assert [r[0] for r in rows] == ["30TXM_0_0-1", "30TXM_0_0-3", "30TXM_0_0-4", "30TXM_0_0-5"]
    assert rows[0][1] == 70  # area-weighted mean of the two copies
    assert abs(rows[0][2] - 41.0) < 1e-9
    assert abs(rows[0][3] - 41.0014) < 1e-9


def _holed_parcel(root: Path) -> None:
    """One 0.002 x 0.002 deg parcel (~37,000 m2) with a ~11 m2 hole and a ~1,130 m2 hole."""
    src = root / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    wkt = (
        "POLYGON((-3.0 41.0, -2.998 41.0, -2.998 41.002, -3.0 41.002, -3.0 41.0),"
        "(-2.9995 41.0005, -2.99995 41.0005, -2.99995 41.00077, -2.9995 41.00077, -2.9995 41.0005),"
        "(-2.9990 41.001, -2.99896 41.001, -2.99896 41.00103, -2.9990 41.00103, -2.9990 41.001))"
    )
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"""COPY (SELECT '30TXM_0_0' AS tile_key, 1::BIGINT AS parcel_id,
          -3.0 AS xmin, 41.0 AS ymin, -2.998 AS xmax, 41.002 AS ymax, 0.6 AS pf_mean,
          false AS touches_window_edge, ST_GeomFromText('{wkt}') AS geometry)
        TO '{src}' (FORMAT parquet)"""
    )
    con.close()


def test_small_interior_holes_are_filled_and_large_ones_kept(tmp_path, monkeypatch):
    """Polygonizing leaves ~3 m2 pixel holes; they are filled, a 1,100 m2 gap is not."""
    _holed_parcel(tmp_path / "merged")
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    dst = fc.convert(2025, "30", 1, "1GB", tmp_path / "out")
    row = pq.read_table(dst).to_pylist()[0]
    geom = shapely.from_wkb(row["geometry"])
    holes = sum(len(p.interiors) for p in getattr(geom, "geoms", [geom]))
    assert holes == 1, "the ~11 m2 hole is filled, the ~1,130 m2 hole stays"
    con = duckdb.connect()
    con.sql("load spatial")
    full = con.sql(
        "select ST_Area(ST_Transform(ST_MakeEnvelope(-3.0, 41.0, -2.998, 41.002), "
        "'EPSG:4326', 'EPSG:32630', true))"
    ).fetchone()[0]
    assert 1000 < full - row["metrics:area"] < 1250, "metrics:area excludes only the kept hole"


def _utm_box_wkt(zone_epsg: int, cx: float, cy: float, w: float, h: float, hole=None) -> str:
    """A UTM box (w x h metres, centred on cx, cy) as lon/lat WKT; ``hole`` is (dx, dy, w, h)."""
    from pyproj import Transformer

    tr = Transformer.from_crs(zone_epsg, 4326, always_xy=True)

    def ring(x0, y0, x1, y1):
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        return "(" + ", ".join("{:.9f} {:.9f}".format(*tr.transform(x, y)) for x, y in pts) + ")"

    rings = [ring(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)]
    if hole:
        dx, dy, hw, hh = hole
        rings.append(ring(cx + dx - hw / 2, cy + dy - hh / 2, cx + dx + hw / 2, cy + dy + hh / 2))
    return "(" + ", ".join(rings) + ")"


def _write_wkt_zone(root: Path, tile_key: str, wkts: list[str], lon: float, lat: float) -> None:
    src = root / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    values = ", ".join(
        f"('{tile_key}', {i}, {lon}::DOUBLE, {lat}::DOUBLE, {lon + 0.01}::DOUBLE, "
        f"{lat + 0.01}::DOUBLE, 0.7::DOUBLE, false, ST_GeomFromText('{w}'))"
        for i, w in enumerate(wkts)
    )
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(f"COPY (SELECT * FROM (VALUES {values}) {BOX_COLS}) TO '{src}' (FORMAT parquet)")
    con.close()


def _holes_by_id(dst: Path) -> dict[str, list[int]]:
    "id -> interior-ring count of each part, for every published row."
    out = {}
    for row in pq.read_table(dst).to_pylist():
        g = shapely.from_wkb(row["geometry"])
        out[row["id"]] = [len(p.interiors) for p in getattr(g, "geoms", [g])]
    return out


@pytest.mark.parametrize(
    ("tile_key", "lon", "lat", "cy"),
    [("30TXM_0_0", -3.0, 41.0, 4_540_000.0), ("30HXM_0_0", -3.0, -41.0, -4_540_000.0)],
    ids=["north", "south"],
)
def test_hole_fill_keeps_every_geometry_shape(tmp_path, monkeypatch, tile_key, lon, lat, cy):
    """One-part and two-part MULTIPOLYGONs survive hole filling; a ring just under 20 m2
    is filled and one just over is kept, in either hemisphere of the zone."""
    cx = 500_000.0  # zone 30 central meridian is -3 deg
    box = (300.0, 300.0)
    small, large = (4.3, 4.3), (4.6, 4.6)  # 18.5 m2 and 21.2 m2
    wkts = [
        f"POLYGON{_utm_box_wkt(32630, cx, cy, *box, hole=(0, 0, *small))}",  # 0: filled
        f"POLYGON{_utm_box_wkt(32630, cx, cy + 1000, *box, hole=(0, 0, *large))}",  # 1: kept
        # 2: single-part MULTIPOLYGON with a small hole (used to be dropped)
        f"MULTIPOLYGON({_utm_box_wkt(32630, cx, cy + 2000, *box, hole=(0, 0, *small))})",
        # 3: single-part MULTIPOLYGON with a large hole
        f"MULTIPOLYGON({_utm_box_wkt(32630, cx, cy + 3000, *box, hole=(0, 0, *large))})",
        # 4: two parts, each with a hole: small filled, large kept
        "MULTIPOLYGON("
        + _utm_box_wkt(32630, cx, cy + 4000, *box, hole=(0, 0, *small))
        + ", "
        + _utm_box_wkt(32630, cx + 600, cy + 4000, *box, hole=(0, 0, *large))
        + ")",
    ]
    _write_wkt_zone(tmp_path / "merged", tile_key, wkts, lon, lat)
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    dst = fc.convert(2025, "30", 1, "1GB", tmp_path / "out")
    holes = _holes_by_id(dst)
    assert holes == {
        f"{tile_key}-0": [0],
        f"{tile_key}-1": [1],
        f"{tile_key}-2": [0],
        f"{tile_key}-3": [1],
        f"{tile_key}-4": [0, 1],
    }, holes


def test_the_part_filter_sees_the_filled_part_like_the_parcel_filter(tmp_path, monkeypatch):
    """MIN_PART_M2 is tested after the fill, so a part and a standalone parcel of the
    identical shape share one fate: 906 m2 gross, 891 m2 net of a 15 m2 hole, both kept."""
    cx, cy = 500_000.0, 4_540_000.0
    small = (30.1, 30.1)  # 906.0 m2 gross, 890.8 m2 net of the hole below
    hole = (0, 0, 3.9, 3.9)  # 15.2 m2, under MIN_HOLE_M2
    wkts = [
        "MULTIPOLYGON("
        + _utm_box_wkt(32630, cx, cy, 300.0, 300.0)
        + ", "
        + _utm_box_wkt(32630, cx + 400, cy, *small, hole=hole)
        + ")",
        f"POLYGON{_utm_box_wkt(32630, cx, cy + 2000, *small, hole=hole)}",
    ]
    _write_wkt_zone(tmp_path / "merged", "30TXM_0_0", wkts, -3.0, 41.0)
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    dst = fc.convert(2025, "30", 1, "1GB", tmp_path / "out")
    assert _holes_by_id(dst) == {"30TXM_0_0-0": [0, 0], "30TXM_0_0-1": [0]}
    areas = {r["id"]: r["metrics:area"] for r in pq.read_table(dst).to_pylist()}
    assert abs(areas["30TXM_0_0-1"] - 906.0) < 1.0, "the standalone keeps its filled area"
    assert abs(areas["30TXM_0_0-0"] - (90_000.0 + 906.0)) < 2.0, "the part is kept, filled"
