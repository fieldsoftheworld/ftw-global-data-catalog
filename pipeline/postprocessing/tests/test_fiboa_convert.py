import importlib.util
import json
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

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
    import pytest

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
