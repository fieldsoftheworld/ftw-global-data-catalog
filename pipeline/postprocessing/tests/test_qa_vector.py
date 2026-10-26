import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import qa_vector as qv


def _zone(root: Path, zone: str, parcels: list[tuple[str, tuple, float]]) -> None:
    d = root / "2025" / f"zone={zone}"
    d.mkdir(parents=True)
    geoms = [shapely.box(*b) for _, b, _ in parcels]
    bbox = pa.array([dict(zip(("xmin", "ymin", "xmax", "ymax"), g.bounds, strict=True))
                     for g in geoms])
    t = pa.table({
        "id": [p[0] for p in parcels],
        "geometry": [shapely.to_wkb(g) for g in geoms],
        "bbox": bbox,
        "metrics:area": pa.array([p[2] for p in parcels], pa.float32()),
    })
    geo = {"version": "1.1.0", "primary_column": "geometry",
           "columns": {"geometry": {"encoding": "WKB", "geometry_types": ["Polygon"]}}}
    pq.write_table(t.replace_schema_metadata({"geo": json.dumps(geo)}), d / f"utm{zone}.parquet")


def _args(**kw) -> argparse.Namespace:
    base = {"max_km2": 5.0, "threads": 1, "memory_limit": "1GB", "land_polygons": None,
            "frames": None, "sea_per_zone": 100, "zone_tol": 0.15}
    return argparse.Namespace(**{**base, **kw})


def test_counts_cap_ids_and_a_field_kept_by_two_zones(tmp_path):
    shared = (-0.0005, 45.0, 0.0005, 45.001)  # straddles the 30|31 line at 0E
    _zone(tmp_path, "30", [("30TYQ_0_0-1", shared, 7000.0),
                           ("30TYQ_0_0-2", (-1.0, 45.0, -0.99, 45.01), 6e6)])
    _zone(tmp_path, "31", [("31TCK_0_0-1", shared, 7000.0),
                           ("31TCK_0_0-1", (1.0, 45.0, 1.001, 45.001), 8000.0)])
    out = qv.run(tmp_path, 2025, _args(), True)
    t = out["totals"]
    assert (t["n"], t["zone_files"], t["over_cap"]) == (4, 2, 1)
    assert t["zones"]["30"]["n"] == 2
    assert out["dup_ids"] == 1
    assert out["cross_zone"]["pairs"] == 1
    assert out["equator_pairs"] == 0
    assert set(out["strips"]) == set(qv.STRIPS)


def test_compare_flags_zones_that_moved():
    new = {"n": 120, "km2": 10.0, "zones": {"30": {"n": 100}, "31": {"n": 20}}}
    old = {"n": 100, "km2": 10.0, "zones": {"30": {"n": 80}, "32": {"n": 20}}}
    c = qv.compare(new, old, 0.15)
    assert c["zones_moved"] == {"30": 0.25}
    assert (c["zones_only_new"], c["zones_only_old"]) == (["31"], ["32"])
