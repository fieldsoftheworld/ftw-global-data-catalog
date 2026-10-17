import argparse
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_outline_codec import _grid_geoms

import compact_outlines as compact


def test_existing_compact_file_verified_before_deleting_original(tmp_path):
    src = tmp_path / "outlines/2025/56JLQ_0_0.parquet"
    dst = tmp_path / "compact/2025/56JLQ_0_0.parquet"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    original = pa.table(
        {"parcel_id": [1], "geometry": shapely.to_wkb(_grid_geoms()[:1])},
        metadata={b"geo": b"{}"},
    )
    pq.write_table(original, src)
    compact.encode_file(src, dst, src.stem)
    updated = original.set_column(0, "parcel_id", pa.array([2]))
    pq.write_table(updated, src)
    args = argparse.Namespace(
        in_root=str(tmp_path / "outlines"),
        out_root=str(tmp_path / "compact"),
        year="2025",
        min_mb=0,
        max_mb=float("inf"),
        num_shards=1,
        shard=0,
        delete_original=True,
    )

    assert compact._encode_year(args) == 1
    assert pq.read_table(src).equals(updated)

    pq.write_table(original, src)
    assert compact._encode_year(args) == 0
    assert not src.exists()
    restored = tmp_path / "restored.parquet"
    compact.decode_file(dst, restored)
    assert pq.read_table(restored).equals(original)


def _compact(tmp_path: Path, geoms: np.ndarray) -> Path:
    src, dst = tmp_path / "src.parquet", tmp_path / "56JLQ_0_0.parquet"
    pq.write_table(
        pa.table(
            {"parcel_id": list(range(len(geoms))), "geometry": shapely.to_wkb(geoms)},
            metadata={b"geo": b"{}"},
        ),
        src,
    )
    compact.encode_file(src, dst, "56JLQ_0_0")
    return dst


def _shift_vertex(geoms: np.ndarray, dx_m: float) -> np.ndarray:
    """Geometries with their second vertex moved by dx_m metres east (through UTM 56)."""
    to_utm, to_ll = (
        Transformer.from_crs(4326, 32756, always_xy=True),
        Transformer.from_crs(32756, 4326, always_xy=True),
    )

    def move(c: np.ndarray) -> np.ndarray:
        x, y = to_utm.transform(c[:, 0], c[:, 1])
        x = np.where(np.arange(len(c)) == 1, x + dx_m, x)
        return np.column_stack(to_ll.transform(x, y))

    return shapely.transform(geoms, move)


def _table(g: np.ndarray) -> pa.Table:
    return pa.table({"parcel_id": list(range(len(g))), "geometry": shapely.to_wkb(g)})


def test_verify_accepts_one_ulp_and_rejects_a_cell(tmp_path):
    geoms = _grid_geoms()
    dst = _compact(tmp_path, geoms)
    # a coordinate one ulp away (another machine's PROJ rounding) still verifies
    compact._verify(dst, _table(shapely.transform(geoms, lambda c: np.nextafter(c, np.inf))), 32756)
    # a vertex one 2.5 m cell away does not
    with pytest.raises(ValueError, match="grid integers differ"):
        compact._verify(dst, _table(_shift_vertex(geoms, 2.5)), 32756)


def test_vertex_between_grid_points_is_refused(tmp_path):
    with pytest.raises(ValueError, match=r"off the 2\.5 m grid"):
        _compact(tmp_path, _shift_vertex(_grid_geoms(), 1.25))
