"""Fill interior rings (holes) under MIN_HOLE_M2 in existing fiboa zone files.

``fiboa_convert.py`` fills these holes for new conversions. This is the contingency
for a *staged* tree converted without that rule, instead of a full reconversion:
exterior rings, row order and bbox are unchanged, rows without small holes keep
their original WKB bytes, metrics:area / metrics:perimeter move by the filled rings'
area and length (measured in the zone's north UTM CRS, as in conversion), and the
footer's ``determination:details`` gains the hole-fill clause so the file's
provenance names the filter that produced it.

**It has no work to do on the 2e release.** Every published zone file sampled
(2017/2021/2024/2025, zones 01 and 57) already has zero interior rings under
MIN_HOLE_M2 -- the smallest measured is 21.875 m2, 3.5 px at 2.5 m -- so the
released data was converted with the rule in force. A file with no small holes is
therefore reported and **not written**: a rewrite is never byte-identical (fresh
zstd-19 compression, fresh footer), and the catalog commits ``file:size`` and a
multihash ``file:checksum`` per zone file, so an identical-content rewrite would
re-upload the file and invalidate its committed checksum for nothing.

Do not point this at the published tree. If a patched file ever does reach
publication, four places record numbers this script changes and none of them
recompute: each item's ``file:size`` / ``file:checksum`` and its
"N parcels, N km2 of field area" description, the collection description's parcel /
area / GiB totals, and ``index/vector.parquet``'s ``size_bytes`` / ``area_km2``
(``tools/rebuild_index.py`` only rewrites hrefs -- it recomputes nothing). Regenerate
all four from the patched files before publishing.

    python fill_small_holes.py --in-root fiboa --out-root fiboa-filled --year 2024 --zone 43
    python fill_small_holes.py --in-root fiboa --out-root fiboa-filled \
        --index $SLURM_ARRAY_TASK_ID          # over every (year, zone) file
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from fiboa_common import MIN_HOLE_M2, MIN_PART_M2, write_sorted

ROW_GROUP = 8192
#: The sentence ``fiboa_convert.collection_metadata`` writes before the hole rule
#: existed, and the clause it gained with it.
PART_SENTENCE = f"parcels and parts under {MIN_PART_M2:g} m2 removed."
HOLE_CLAUSE = f"interior holes under {MIN_HOLE_M2:g} m2 filled"


def zone_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/zone=*/utm*.parquet"))


def to_utm(geoms: np.ndarray, tr: Transformer) -> np.ndarray:
    return shapely.transform(geoms, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def small_rings(geoms: np.ndarray, tr: Transformer):
    """Every interior ring of ``geoms``, flattened, with the small ones flagged.

    Returns ``(parts, row_of_part, part_of_ring, rings, area, length, small)``, or
    ``None`` when no geometry has an interior ring at all.
    """
    parts, row_of_part = shapely.get_parts(geoms, return_index=True)
    n_holes = shapely.get_num_interior_rings(parts)
    holed = np.flatnonzero(n_holes > 0)
    if holed.size == 0:
        return None
    part_of_ring = np.repeat(holed, n_holes[holed])
    k = np.concatenate([np.arange(n) for n in n_holes[holed]])
    rings = shapely.get_interior_ring(parts[part_of_ring], k)
    utm = to_utm(shapely.polygons(rings), tr)
    area, length = shapely.area(utm), shapely.length(utm)
    return parts, row_of_part, part_of_ring, rings, area, length, area < MIN_HOLE_M2


def count_small_holes(src: Path, tr: Transformer) -> int:
    "Small interior rings in ``src``, counted without rewriting it."
    n = 0
    pf = pq.ParquetFile(src)
    for b in pf.iter_batches(batch_size=65536, columns=["geometry"]):
        wkb = b.column("geometry").to_numpy(zero_copy_only=False)
        found = small_rings(shapely.from_wkb(wkb), tr)
        if found:
            n += int(found[-1].sum())
    return n


def restamp(schema: pa.Schema) -> pa.Schema:
    """``schema`` with the hole rule added to the footer's ``determination:details``.

    A patched file has to carry the provenance of the filter that produced it:
    ``tools/build_vector_items.py`` generates every published item description,
    collection description and per-year README straight from this footer. A details
    string this does not recognise is one it cannot correct, so it exits rather than
    stage a file whose provenance would be wrong.
    """
    md = dict(schema.metadata or {})
    coll = json.loads(md.get(b"collection", b"{}"))
    details = coll.get("determination:details", "")
    if HOLE_CLAUSE in details:
        return schema
    if PART_SENTENCE not in details:
        sys.exit(
            f"determination:details does not contain {PART_SENTENCE!r}, so the hole rule "
            f"cannot be stamped into it; refusing to patch. Footer: {details!r}"
        )
    coll["determination:details"] = details.replace(
        PART_SENTENCE, f"{PART_SENTENCE.rstrip('.')}, {HOLE_CLAUSE}."
    )
    md[b"collection"] = json.dumps(coll).encode()
    return schema.with_metadata(md)


def fill_batch(batch: pa.RecordBatch, tr: Transformer) -> tuple[pa.RecordBatch, int]:
    "Fill the small holes of one batch; returns the batch and the number of rings filled."
    geoms = shapely.from_wkb(batch.column("geometry").to_numpy(zero_copy_only=False))
    found = small_rings(geoms, tr)
    if found is None:
        return batch, 0
    parts, row_of_part, part_of_ring, rings, area, length, small = found
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
    """Write ``src`` to ``dst`` with small holes filled; returns the number of rings filled.

    Counts the small rings first and leaves ``dst`` unwritten when there are none: a
    rewrite changes the bytes (and so the published size and checksum) even when it
    changes no geometry. See the module docstring.
    """
    zone = int(src.parent.name.split("=")[1])
    tr = Transformer.from_crs(4326, 32600 + zone, always_xy=True)
    pf = pq.ParquetFile(src)
    label = f"{src.parent.parent.name}/{src.parent.name}"
    if not count_small_holes(src, tr):
        print(
            f"{label}: {pf.metadata.num_rows:,} rows, no holes < {MIN_HOLE_M2:g} m2 "
            f"-- unchanged, {dst} not written",
            flush=True,
        )
        return 0
    schema = restamp(pf.schema_arrow)  # keeps geo metadata (bbox is unchanged)
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
        f"{label}: {n:,} rows, {filled:,} holes < {MIN_HOLE_M2:g} m2 filled",
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
        files = zone_files(a.in_root)
        if not 0 <= a.index < len(files):
            sys.exit(f"--index {a.index} outside 0-{len(files) - 1} under {a.in_root}")
        src = files[a.index]
    elif a.year and a.zone:
        src = a.in_root / str(a.year) / f"zone={a.zone:02d}" / f"utm{a.zone:02d}.parquet"
    else:
        sys.exit("pass --index or --year and --zone")
    patch(src, a.out_root / src.relative_to(a.in_root))


if __name__ == "__main__":
    main()
