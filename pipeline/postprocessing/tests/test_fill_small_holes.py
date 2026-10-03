import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
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


def _area(g, tr: Transformer) -> float:
    return float(shapely.area(fsh.to_utm(g, tr)))


def _zone_file(root: Path, geoms: list) -> Path:
    src = root / "2025" / "zone=30" / "utm30.parquet"
    src.parent.mkdir(parents=True)
    tr = Transformer.from_crs(4326, 32630, always_xy=True)
    areas = [_area(g, tr) for g in geoms]
    n = len(geoms)
    tbl = pa.table(
        {
            "id": [f"t-{i}" for i in range(n)],
            "geometry": [shapely.to_wkb(g) for g in geoms],
            "bbox": [
                dict(zip(("xmin", "ymin", "xmax", "ymax"), g.bounds, strict=True)) for g in geoms
            ],
            "metrics:area": pa.array(areas, pa.float32()),
            "metrics:perimeter": pa.array([1.0] * n, pa.float32()),
        }
    )
    pq.write_table(tbl.replace_schema_metadata({b"collection": b"{}"}), src)
    return src


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
    # the filled ring's area (~11 m2) moves metrics:area; the perimeter shrinks
    d_area = after[0]["metrics:area"] - before[0]["metrics:area"]
    assert 9 < d_area < 14
    assert after[0]["metrics:perimeter"] < before[0]["metrics:perimeter"]
    assert pq.read_schema(dst).metadata[b"collection"] == b"{}"
