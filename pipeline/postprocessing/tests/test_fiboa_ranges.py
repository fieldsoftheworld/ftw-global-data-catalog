import argparse
import json
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fiboa_ranges as fr

fc = fr.fc


def _write_zone(root: Path) -> Path:
    """A 30 x 10 grid of boxes in tile 30TXM, a seam duplicate with 30TYM, and a holed box."""
    src = root / "2025" / "zone=30" / "part-0.parquet"
    src.parent.mkdir(parents=True)
    rows = [
        ("30TXM_0_0", 100000 + i * 20 + j, -3.0 + i * 0.002, 41.0 + j * 0.002, 0.7)
        for i in range(30)
        for j in range(10)
    ]
    rows = [(t, p, x, y, x + 0.001, y + 0.001, pf) for t, p, x, y, pf in rows]
    rows += [
        ("30TXM_0_0", 1, -3.0110, 41.000, -3.0100, 41.001, 0.6),
        ("30TYM_0_0", 1, -3.0102, 41.000, -3.0092, 41.001, 0.8),
        ("30TYM_0_0", 2, -2.9000, 41.000, -2.8990, 41.001, 0.4),
    ]
    geoms = {r[:2]: f"ST_MakeEnvelope({r[2]}, {r[3]}, {r[4]}, {r[5]})" for r in rows}
    geoms[("30TXM_0_0", 100000)] = (
        "ST_GeomFromText('POLYGON((-3.0 41.0, -2.999 41.0, -2.999 41.001, -3.0 41.001, -3.0 41.0),"
        "(-2.99951 41.00051, -2.99949 41.00051, -2.99949 41.00049, -2.99951 41.00049, "
        "-2.99951 41.00051))')"
    )
    values = ", ".join(
        f"('{t}', {p}, {x0}::DOUBLE, {y0}::DOUBLE, {x1}::DOUBLE, {y1}::DOUBLE, {pf}::DOUBLE, "
        f"{geoms[(t, p)]})"
        for t, p, x0, y0, x1, y1, pf in rows
    )
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"COPY (SELECT *, false AS touches_window_edge FROM (VALUES {values}) "
        f"t(tile_key, parcel_id, xmin, ymin, xmax, ymax, pf_mean, geometry)) "
        f"TO '{src}' (FORMAT parquet)"
    )
    return src


@pytest.fixture
def env(tmp_path, monkeypatch):
    _write_zone(tmp_path / "merged")
    monkeypatch.setattr(fc, "IN_ROOT", tmp_path / "merged")
    monkeypatch.setattr(fc, "TMP_ROOT", tmp_path / "duck")
    monkeypatch.setattr(fc, "ROW_GROUP", 50)
    return tmp_path


def _args(tmp_path: Path, **kw) -> argparse.Namespace:
    base = {"year": 2025, "zone": "30", "work_root": tmp_path / "work",
            "out_root": tmp_path / "out", "threads": 1, "memory_limit": "1GB", "ranges": 3,
            "keep": True, "spec": None, "land_polygons": None, "no_sea_filter": True}
    return argparse.Namespace(**{**base, **kw})


def _run_ranges(tmp_path: Path, **kw) -> Path:
    fr.plan(_args(tmp_path, **kw))
    for k in range(kw.get("ranges", 3)):
        fr.range_task(_args(tmp_path, k=k, **kw))
    fr.stitch_task(_args(tmp_path, **kw))
    return tmp_path / "out" / "2025" / "zone=30" / "utm30.parquet"


def test_ranges_are_byte_identical_to_the_whole_zone(env):
    whole = fc.convert(2025, "30", 1, "1GB", env / "whole")
    ranged = _run_ranges(env)
    assert pq.ParquetFile(whole).metadata.num_rows == 300 + 2
    assert whole.read_bytes() == ranged.read_bytes()


@pytest.mark.parametrize("ranges", [1, 2, 7])
def test_any_range_count_gives_the_same_file(env, ranges):
    whole = fc.convert(2025, "30", 1, "1GB", env / "whole")
    assert whole.read_bytes() == _run_ranges(env, ranges=ranges).read_bytes()


def test_merged_keeps_the_global_order_across_unsorted_buckets(tmp_path):
    streams = []
    for i, (hks, ids) in enumerate((([1, 4, 4, 9], "abde"), ([2, 4, 8], "cfa"), ([], ""))):
        p = tmp_path / f"s{i}.parquet"
        pq.write_table(pa.table({"hk": pa.array(hks, pa.uint32()),
                                 "id": pa.array(list(ids), pa.string())}), p)
        streams.append(p)
    out = pa.Table.from_batches(list(fr.merged(streams, 2)))
    assert list(zip(out["hk"].to_pylist(), out["id"].to_pylist(), strict=True)) == [
        (1, "a"), (2, "c"), (4, "b"), (4, "d"), (4, "f"), (8, "a"), (9, "e")
    ]


def test_stitch_requires_a_record_for_every_range(env):
    fr.plan(_args(env))
    for k in (0, 2):
        fr.range_task(_args(env, k=k))
    with pytest.raises(SystemExit, match="range 1: no done record"):
        fr.stitch_task(_args(env))
    assert not (env / "out" / "2025" / "zone=30" / "utm30.parquet").exists()


def test_stitch_rejects_a_missing_or_stale_part(env):
    fr.plan(_args(env))
    for k in range(3):
        fr.range_task(_args(env, k=k))
    wd = env / "work" / "2025" / "30"
    (wd / "part-0001.parquet").unlink()
    with pytest.raises(SystemExit, match="range 1: no part"):
        fr.stitch_task(_args(env))
    fr.range_task(_args(env, k=1))
    rec = json.loads((wd / "done-0002.json").read_text())
    rec["raw_rows"] += 1
    (wd / "done-0002.json").write_text(json.dumps(rec))
    with pytest.raises(SystemExit, match="range 2: done record does not match"):
        fr.stitch_task(_args(env))


def test_range_rejects_a_tampered_plan(env):
    fr.plan(_args(env))
    plan = env / "work" / "2025" / "30" / "plan.json"
    pj = json.loads(plan.read_text())
    pj["raw_rows"][0] += 5
    plan.write_text(json.dumps(pj))
    with pytest.raises(SystemExit, match="raw rows on disk"):
        fr.range_task(_args(env, k=0))


def test_ranges_apply_the_sea_rule_like_the_whole_zone(env):
    g = shapely.box(-3.1, 40.9, -2.97, 41.1)  # land only west of -2.97: the rest is sea
    land = env / "land.parquet"
    bounds = dict(zip(("xmin", "ymin", "xmax", "ymax"), g.bounds, strict=True))
    pq.write_table(
        pa.table({"geometry": [shapely.to_wkb(g)], **{k: [v] for k, v in bounds.items()}}), land
    )
    whole = fc.convert(2025, "30", 1, "1GB", env / "whole", land=land)
    ranged = _run_ranges(env, land_polygons=land, no_sea_filter=False)
    assert whole.read_bytes() == ranged.read_bytes()
    xmax = duckdb.sql(f"select max(bbox.xmax) from '{ranged}'").fetchall()[0][0]
    assert xmax <= -2.97 + 0.001
    details = json.loads(pq.ParquetFile(ranged).schema_arrow.metadata[b"collection"])
    assert "OpenStreetMap land" in details["determination:details"]


def test_tiers_size_by_rows():
    from fiboa_tiers import tier

    assert tier(285_000)[0] == "xs"
    assert tier(880_000)[0] == "s"
    assert tier(2_200_000)[0] == "m"
