import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parents[1]))
import fill_small_holes as fsh

SHELL = [(-3.0, 41.0), (-2.998, 41.0), (-2.998, 41.002), (-3.0, 41.002)]
SMALL = [(-2.9990, 41.001), (-2.99896, 41.001), (-2.99896, 41.00103), (-2.9990, 41.00103)]  # ~11 m2
LARGE = [
    (-2.9995, 41.0005),
    (-2.99995, 41.0005),
    (-2.99995, 41.00077),
    (-2.9995, 41.00077),
]  # ~1,100 m2
#: The same shapes 0.003 deg east, for the second part of a MULTIPOLYGON.
SHELL2 = [(x + 0.003, y) for x, y in SHELL]
SMALL2 = [(x + 0.003, y) for x, y in SMALL]
#: A footer as ``fiboa_convert`` wrote it before the hole rule existed.
PRE_FIX_DETAILS = (
    "Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics, "
    "2.5 m field/boundary probabilities, 5 m coverage simplification, "
    f"parcels > 5 km2 removed, {fsh.PART_SENTENCE} Attributes are for filtering."
)
TR = Transformer.from_crs(4326, 32630, always_xy=True)


def _utm(g, tr: Transformer = TR):
    return fsh.to_utm(g, tr)


def _parts(g) -> list:
    return list(getattr(g, "geoms", [g]))


def _zone_file(root: Path, geoms: list) -> Path:
    src = root / "2025" / "zone=30" / "utm30.parquet"
    src.parent.mkdir(parents=True)
    n = len(geoms)
    tbl = pa.table(
        {
            "id": [f"t-{i}" for i in range(n)],
            "geometry": [shapely.to_wkb(g) for g in geoms],
            "bbox": [
                dict(zip(("xmin", "ymin", "xmax", "ymax"), g.bounds, strict=True)) for g in geoms
            ],
            # both metrics are seeded from the true UTM values, so a wrong adjustment
            # cannot hide behind a placeholder
            "metrics:area": pa.array([float(shapely.area(_utm(g))) for g in geoms], pa.float32()),
            "metrics:perimeter": pa.array(
                [float(shapely.length(_utm(g))) for g in geoms], pa.float32()
            ),
        }
    )
    meta = {b"collection": json.dumps({"determination:details": PRE_FIX_DETAILS}).encode()}
    pq.write_table(tbl.replace_schema_metadata(meta), src)
    return src


def _details(path: Path) -> str:
    return json.loads(pq.read_schema(path).metadata[b"collection"])["determination:details"]


def test_small_holes_filled_large_kept_and_untouched_rows_byte_identical(tmp_path):
    holed = shapely.Polygon(SHELL, [SMALL, LARGE])
    plain = shapely.Polygon(SHELL, [LARGE])
    solid = shapely.Polygon(SHELL)
    src = _zone_file(tmp_path / "in", [holed, plain, solid])
    dst = tmp_path / "out" / "2025" / "zone=30" / "utm30.parquet"
    assert fsh.patch(src, dst) == 1
    before, after = pq.read_table(src).to_pylist(), pq.read_table(dst).to_pylist()
    assert [r["id"] for r in after] == [r["id"] for r in before]
    got = shapely.from_wkb(after[0]["geometry"])
    assert len(got.interiors) == 1
    assert shapely.equals_exact(got, plain, 1e-12)
    # rows that lose nothing keep their exact bytes and metrics
    assert after[1] == before[1] and after[2] == before[2]
    # the filled ring's own area and length are what moves, nothing else
    ring = shapely.Polygon(SMALL)
    ring_area, ring_len = float(shapely.area(_utm(ring))), float(shapely.length(_utm(ring)))
    assert 9 < ring_area < 14 and 12 < ring_len < 15, "fixture: a ~11 m2, ~13.5 m ring"
    assert abs((after[0]["metrics:area"] - before[0]["metrics:area"]) - ring_area) < 0.05
    assert abs((before[0]["metrics:perimeter"] - after[0]["metrics:perimeter"]) - ring_len) < 0.05


def test_a_one_part_multipolygon_stays_a_multipolygon(tmp_path):
    "A one-part MULTIPOLYGON is not a Polygon; the rebuild must not flatten it."
    g = shapely.MultiPolygon([shapely.Polygon(SHELL, [SMALL])])
    src = _zone_file(tmp_path / "in", [g])
    dst = tmp_path / "out" / "2025" / "zone=30" / "utm30.parquet"
    assert fsh.patch(src, dst) == 1
    got = shapely.from_wkb(pq.read_table(dst).to_pylist()[0]["geometry"])
    assert got.geom_type == "MultiPolygon"
    assert [len(p.interiors) for p in _parts(got)] == [0]
    assert shapely.equals_exact(got, shapely.MultiPolygon([shapely.Polygon(SHELL)]), 1e-12)


def test_a_multipart_multipolygon_keeps_every_part_in_order(tmp_path):
    "Only the part that loses a ring is rebuilt; the others survive, in place."
    g = shapely.MultiPolygon([shapely.Polygon(SHELL, [LARGE]), shapely.Polygon(SHELL2, [SMALL2])])
    src = _zone_file(tmp_path / "in", [g])
    dst = tmp_path / "out" / "2025" / "zone=30" / "utm30.parquet"
    assert fsh.patch(src, dst) == 1
    row = pq.read_table(dst).to_pylist()[0]
    got = shapely.from_wkb(row["geometry"])
    assert got.geom_type == "MultiPolygon"
    parts = _parts(got)
    assert len(parts) == 2, "the untouched part is not dropped"
    assert [len(p.interiors) for p in parts] == [1, 0]
    assert [list(p.exterior.coords) for p in parts] == [
        list(p.exterior.coords) for p in _parts(g)
    ], "part order and exteriors unchanged"
    expect = shapely.MultiPolygon([shapely.Polygon(SHELL, [LARGE]), shapely.Polygon(SHELL2)])
    assert abs(row["metrics:area"] - float(shapely.area(_utm(expect)))) < 0.05
    assert abs(row["metrics:perimeter"] - float(shapely.length(_utm(expect)))) < 0.05


def test_a_file_with_no_small_holes_is_not_rewritten(tmp_path):
    """A rewrite is never byte-identical, and the catalog commits size + checksum per
    zone file, so a no-op patch must leave no output behind."""
    src = _zone_file(tmp_path / "in", [shapely.Polygon(SHELL, [LARGE]), shapely.Polygon(SHELL)])
    dst = tmp_path / "out" / "2025" / "zone=30" / "utm30.parquet"
    assert fsh.patch(src, dst) == 0
    assert not dst.exists()


def test_the_patched_footer_names_the_hole_rule(tmp_path):
    "build_vector_items generates every published description from this footer."
    src = _zone_file(tmp_path / "in", [shapely.Polygon(SHELL, [SMALL])])
    dst = tmp_path / "out" / "2025" / "zone=30" / "utm30.parquet"
    assert fsh.patch(src, dst) == 1
    assert fsh.HOLE_CLAUSE not in _details(src)
    assert f"{fsh.PART_SENTENCE.rstrip('.')}, {fsh.HOLE_CLAUSE}." in _details(dst)
    assert json.loads(pq.read_schema(dst).metadata[b"collection"]).keys() == {
        "determination:details"
    }


def test_an_unrecognised_footer_is_refused_rather_than_published(tmp_path):
    schema = pa.schema([pa.field("geometry", pa.binary())]).with_metadata(
        {b"collection": json.dumps({"determination:details": "something else"}).encode()}
    )
    with pytest.raises(SystemExit) as e:
        fsh.restamp(schema)
    assert fsh.PART_SENTENCE in str(e.value)


def test_an_already_stamped_footer_is_left_alone():
    details = f"{fsh.PART_SENTENCE.rstrip('.')}, {fsh.HOLE_CLAUSE}."
    schema = pa.schema([pa.field("geometry", pa.binary())]).with_metadata(
        {b"collection": json.dumps({"determination:details": details}).encode()}
    )
    assert fsh.restamp(schema) is schema


def test_an_out_of_range_index_names_the_cause(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["fill_small_holes.py", "--in-root", str(tmp_path), "--out-root", str(tmp_path / "o"),
         "--index", "0"],
    )
    with pytest.raises(SystemExit) as e:
        fsh.main()
    assert "--index 0 outside" in str(e.value)
