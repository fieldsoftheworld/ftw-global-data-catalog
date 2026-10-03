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


def _write(path: Path, *, area: float = 100.0, score: int = 50, compression: str = "zstd"):
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
    assert "non-positive area" in text
    assert "score range" in text
    assert "not in its hive dir" in text
