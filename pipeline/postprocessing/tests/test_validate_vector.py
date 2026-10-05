import json
import random
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

sys.path.insert(0, str(Path(__file__).parents[1]))
import validate_vector as vv

GEO = {"columns": {"geometry": {"crs": {"id": {"authority": "EPSG", "code": 4326}}}}}


def _write(
    path: Path,
    *,
    area: float | None = 100.0,
    score: int = 50,
    compression: str = "zstd",
    drop: str | None = None,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    box = shapely.box(0, 0, 1, 1)
    bbox = {"xmin": 0.0, "ymin": 0.0, "xmax": 1.0, "ymax": 1.0}
    tbl = pa.table(
        {
            "id": ["a-1", "a-2"],
            "collection": ["c", "c"],
            "geometry": [shapely.to_wkb(box)] * 2,
            "bbox": [bbox] * 2,
            "metrics:area": pa.array([area, area], pa.float32()),
            "metrics:perimeter": pa.array([4.0, 4.0], pa.float32()),
            "score": pa.array([score, score], pa.uint8()),
            "determination:datetime": pa.array([0, 0], pa.timestamp("ms", tz="UTC")),
            "determination:method": ["auto-imagery"] * 2,
        }
    )
    if drop is not None:
        tbl = tbl.drop_columns(drop)
    tbl = tbl.replace_schema_metadata({b"geo": json.dumps(GEO).encode()})
    pq.write_table(tbl, path, compression=compression)


def test_good_year_passes(tmp_path):
    _write(tmp_path / "2025" / "zone=30" / "utm30.parquet")
    n, rows, _, _, errs = vv.check_year(tmp_path, "2025", 10, 1, random.Random(0))
    assert (n, rows, errs) == (1, 2, [])


def test_bad_files_are_reported(tmp_path):
    _write(
        tmp_path / "2025" / "zone=30" / "utm30.parquet", area=0.0, score=101, compression="snappy"
    )
    _write(tmp_path / "2025" / "zone=31" / "utm32.parquet")
    *_, errs = vv.check_year(tmp_path, "2025", 10, 54, random.Random(0))
    text = "\n".join(errs)
    assert "2 zone files, expected 54" in text
    assert "compression" in text
    assert "non-positive or null area" in text
    assert "score range" in text
    assert "not in its hive dir" in text


def test_a_missing_column_is_reported_not_raised(tmp_path):
    "Schema drift is the defect this exists to catch; sampling it must not raise."
    _write(tmp_path / "2025" / "zone=30" / "utm30.parquet", drop="score")
    _write(tmp_path / "2025" / "zone=31" / "utm31.parquet", score=101)
    *_, errs = vv.check_year(tmp_path, "2025", 10, 2, random.Random(0))
    text = "\n".join(errs)
    assert "zone=30/utm30.parquet: columns" in text
    assert "score range" in text, "the later file is still checked"


def test_null_area_is_reported(tmp_path):
    _write(tmp_path / "2025" / "zone=30" / "utm30.parquet", area=None)
    *_, errs = vv.check_year(tmp_path, "2025", 10, 1, random.Random(0))
    assert "non-positive or null area" in "\n".join(errs)


def test_an_unreadable_file_is_reported_not_raised(tmp_path):
    bad = tmp_path / "2025" / "zone=30" / "utm30.parquet"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not parquet")
    _write(tmp_path / "2025" / "zone=31" / "utm31.parquet", score=101)
    *_, errs = vv.check_year(tmp_path, "2025", 10, 2, random.Random(0))
    text = "\n".join(errs)
    assert "zone=30/utm30.parquet: unreadable" in text
    assert "score range" in text, "the later file is still checked"
