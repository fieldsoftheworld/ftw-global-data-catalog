#!/usr/bin/env python3
"""The raster-lite index contract: columns, types, order, and bbox rounding.

Builds a lite index from a small synthetic raster index. No network, no AWS.

Run: python3 tests/test_raster_index_lite.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_raster_index_lite import build_lite, outward_f32, write_lite  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


# float64 values with no exact float32 representation
rng = np.random.default_rng(0)
xmin = rng.uniform(-180, 170, 500)
ymin = rng.uniform(-60, 70, 500)
for up in (False, True):
    out = outward_f32(xmin, up=up).astype(np.float64)
    check(bool(np.all(out >= xmin) if up else np.all(out <= xmin)), f"outward rounding up={up}")
    check(bool(np.all(np.abs(out - xmin) < 1e-4)), f"rounding stays within an ulp up={up}")

full = pa.table(
    {
        "year": pa.array([2025, 2017, 2025, 2017], pa.int64()),
        "tile_key": ["33UUU_0_0", "33UUU_0_0", "01KFS_0_0", "01KFS_0_0"],
        "epsg": pa.array([32633, 32633, 32701, 32701], pa.int64()),
        "href": ["a", "b", "c", "d"],
        "size_bytes": pa.array([1, 2, 3, 4], pa.int64()),
        "field_frac": [0.1, 0.2, 0.3, 0.4],
        "xmin": [12.1234567891, 12.1234567891, 177.1234567891, 177.1234567891],
        "ymin": [48.9876543211, 48.9876543211, -17.9876543211, -17.9876543211],
        "xmax": [13.5555555559, 13.5555555559, 178.5555555559, 178.5555555559],
        "ymax": [49.6666666669, 49.6666666669, -17.2222222221, -17.2222222221],
    }
)
lite = build_lite(full)

check(lite.column_names == ["year", "tile_key", "epsg", "xmin", "ymin", "xmax", "ymax"],
      f"columns {lite.column_names}")
check(lite.schema.field("year").type == pa.int16(), "year is int16")
check(lite.schema.field("epsg").type == pa.int32(), "epsg is int32")
check(all(lite.schema.field(c).type == pa.float32() for c in ("xmin", "ymin", "xmax", "ymax")),
      "bbox is float32")
check(lite["tile_key"].to_pylist() == ["01KFS_0_0"] * 2 + ["33UUU_0_0"] * 2, "sorted by tile_key")
check(lite["year"].to_pylist() == [2017, 2025, 2017, 2025], "then by year")
check(lite.num_rows == full.num_rows, "one row per input row")

# the float32 box contains the float64 box, so a tile is never culled early
src = full.sort_by([("tile_key", "ascending"), ("year", "ascending")])
for lo, hi, what in (("xmin", "xmax", "x"), ("ymin", "ymax", "y")):
    check(bool(np.all(lite[lo].to_numpy().astype(np.float64) <= src[lo].to_numpy())),
          f"{lo} rounded down")
    check(bool(np.all(lite[hi].to_numpy().astype(np.float64) >= src[hi].to_numpy())),
          f"{hi} rounded up ({what})")

with tempfile.TemporaryDirectory() as tmp:
    dst = Path(tmp) / "index" / "raster-lite.parquet"
    write_lite(lite, dst)
    md = pq.ParquetFile(dst).metadata
    check(md.row_group(0).column(0).compression == "ZSTD", "ZSTD")
    check(not md.row_group(0).column(0).is_stats_set, "no column statistics")
    check(pq.read_table(dst).equals(lite), "round trip")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: raster-lite index contract holds")
