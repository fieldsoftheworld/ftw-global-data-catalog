"""Fill interior rings (holes) under MIN_HOLE_M2 in existing fiboa zone files.

``fiboa_convert.py`` fills these holes for new conversions. This patches files
converted before that rule existed, instead of a full reconversion: exterior rings,
row order, bbox and schema metadata are unchanged, rows without small holes keep
their original WKB bytes, and metrics:area / metrics:perimeter move by the filled
rings' area and length (measured in the zone's north UTM CRS, as in conversion).

    python fill_small_holes.py --in-root fiboa --out-root fiboa-filled --year 2024 --zone 43
    python fill_small_holes.py --in-root fiboa --out-root fiboa-filled \
        --index $SLURM_ARRAY_TASK_ID          # over every (year, zone) file
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from fiboa_common import MIN_HOLE_M2, write_sorted

ROW_GROUP = 8192


def zone_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/zone=*/utm*.parquet"))


def to_utm(geoms: np.ndarray, tr: Transformer) -> np.ndarray:
    return shapely.transform(geoms, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def fill_batch(batch: pa.RecordBatch, tr: Transformer) -> tuple[pa.RecordBatch, int]:
    "Fill the small holes of one batch; returns the batch and the number of rings filled."
    geoms = shapely.from_wkb(batch.column("geometry").to_numpy(zero_copy_only=False))
    parts, row_of_part = shapely.get_parts(geoms, return_index=True)
    n_holes = shapely.get_num_interior_rings(parts)
    holed = np.flatnonzero(n_holes > 0)
    if holed.size == 0:
        return batch, 0

    # every interior ring of every holed part, flattened
    part_of_ring = np.repeat(holed, n_holes[holed])
    k = np.concatenate([np.arange(n) for n in n_holes[holed]])
    rings = shapely.get_interior_ring(parts[part_of_ring], k)
    utm = to_utm(shapely.polygons(rings), tr)
    area, length = shapely.area(utm), shapely.length(utm)
    small = area < MIN_HOLE_M2
    if not small.any():
        return batch, 0

    # rebuild only parts that lose a hole; then the rows that own them
    new_parts = parts.copy()
    for p in np.unique(part_of_ring[small]):
        sel = part_of_ring == p
        keep = rings[sel][~small[sel]]
        new_parts[p] = shapely.Polygon(shapely.get_exterior_ring(parts[p]), list(keep))
    rows = np.unique(row_of_part[np.unique(part_of_ring[small])])
    wkb = batch.column("geometry").to_pylist()
    for r in rows:
        ps = new_parts[row_of_part == r]
        g = ps[0] if shapely.get_type_id(geoms[r]) == 3 else shapely.MultiPolygon(list(ps))
        wkb[r] = shapely.to_wkb(g)

    nrow = batch.num_rows
    d_area = np.bincount(row_of_part[part_of_ring[small]], weights=area[small], minlength=nrow)
    d_len = np.bincount(row_of_part[part_of_ring[small]], weights=length[small], minlength=nrow)
    cols = {name: batch.column(name) for name in batch.schema.names}
    cols["geometry"] = pa.array(wkb, pa.binary())
    a0 = cols["metrics:area"].to_numpy(zero_copy_only=False).astype(np.float64)
    l0 = cols["metrics:perimeter"].to_numpy(zero_copy_only=False).astype(np.float64)
    cols["metrics:area"] = pa.array((a0 + d_area).astype(np.float32))
    cols["metrics:perimeter"] = pa.array((l0 - d_len).astype(np.float32))
    return pa.RecordBatch.from_pydict(cols, schema=batch.schema), int(small.sum())


def patch(src: Path, dst: Path) -> int:
    "Write ``src`` to ``dst`` with small holes filled; returns the number of rings filled."
    zone = int(src.parent.name.split("=")[1])
    tr = Transformer.from_crs(4326, 32600 + zone, always_xy=True)
    pf = pq.ParquetFile(src)
    schema = pf.schema_arrow  # keeps collection + geo metadata (bbox is unchanged)
    dst.parent.mkdir(parents=True, exist_ok=True)
    filled = 0

    def batches():
        nonlocal filled
        for b in pf.iter_batches(batch_size=65536):
            out, n = fill_batch(b, tr)
            filled += n
            yield out

    n = write_sorted(batches(), schema, dst, ROW_GROUP)
    print(
        f"{src.parent.parent.name}/{src.parent.name}: {n:,} rows, "
        f"{filled:,} holes < {MIN_HOLE_M2:g} m2 filled",
        flush=True,
    )
    return filled


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--year", type=int)
    ap.add_argument("--zone", type=int)
    ap.add_argument("--index", type=int, help="index into sorted (year, zone) files")
    ap.add_argument(
        "--in-root", type=Path, required=True, help="{root}/{year}/zone=NN/utmNN.parquet"
    )
    ap.add_argument("--out-root", type=Path, required=True)
    a = ap.parse_args()
    if a.in_root.resolve() == a.out_root.resolve():
        sys.exit("--out-root must differ from --in-root")
    if a.index is not None:
        src = zone_files(a.in_root)[a.index]
    elif a.year and a.zone:
        src = a.in_root / str(a.year) / f"zone={a.zone:02d}" / f"utm{a.zone:02d}.parquet"
    else:
        sys.exit("pass --index or --year and --zone")
    patch(src, a.out_root / src.relative_to(a.in_root))


if __name__ == "__main__":
    main()
