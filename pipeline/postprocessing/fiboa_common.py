"Shared fiboa SQL and streaming writers."

import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

DET_METHOD = "auto-imagery"


def discover_zones(root: Path, year: int) -> list[str]:
    zones = sorted(p.name.split("=")[1] for p in (root / str(year)).glob("zone=*"))
    if not zones:
        sys.exit(f"no zone=* directories under {root / str(year)}")
    return zones


def zone_at(zones: list[str], index: int) -> str:
    if not 0 <= index < len(zones):
        sys.exit(f"zone index {index} outside 0-{len(zones) - 1}")
    return zones[index]


def connect(threads: int, memory_limit: str, tmp_dir: Path) -> duckdb.DuckDBPyConnection:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql(
        f"load spatial; set threads={threads}; set enable_progress_bar=false; "
        f"set memory_limit='{memory_limit}'; set temp_directory='{tmp_dir}'; "
        f"set preserve_insertion_order=false"
    )
    return con


def zone_utm(geom: str) -> str:
    """The zone's **north** UTM CRS, for every metric and every geometry comparison.

    326NN and 327NN differ only by a 10,000,000 m false northing, so areas,
    perimeters and intersections are identical in either (measured: 49196.90717479
    vs 49196.90717492 m2 for the same equator-straddling box). A *per-row*
    hemisphere EPSG, however, places the two halves of an equator-straddling field
    10,000 km apart -- northing -221.06 in 32630 against 9,999,778.94 in 32730 --
    so ST_Intersects can never match them and no cross-equator seam join fires.
    One CRS per zone keeps the arithmetic identical and the comparison meaningful.
    """
    return f"ST_Transform({geom}, 'EPSG:4326', 'EPSG:326' || tile_key[1:2], true)"


def hk_expr(bbox: list[float]) -> str:
    "Hilbert index of the bbox centre on a square curve so cells stay square in degrees."
    w, s, e, n = bbox
    side = max(e - w, n - s, 1e-6)
    curve = f"{{min_x: {w}, min_y: {s}, max_x: {w + side}, max_y: {s + side}}}::BOX_2D"
    return f"ST_Hilbert((xmin + xmax) / 2, (ymin + ymax) / 2, {curve})"


#: Minimum published parcel area and minimum kept part of a multipart parcel, m2.
MIN_PARCEL_M2 = 900.0
MIN_PART_M2 = 900.0
#: Interior rings (holes) under this area are filled, m2: ~3 px at 2.5 m. Polygonizing
#: leaves half-pixel (3.125 m2) holes inside ~40% of parcels; larger holes (farm
#: buildings, ponds, a neighbouring field) are kept.
MIN_HOLE_M2 = 20.0


def drop_small_holes(poly: str) -> str:
    """SQL rebuilding the simple Polygon ``poly`` without interior rings under ``MIN_HOLE_M2``.

    Each ring's area is measured in the zone's north UTM CRS (``zone_utm``). The
    ``ST_NumInteriorRings = 0`` guard skips the rebuild (and ST_MakePolygon, which
    rejects a degenerate shell) for the common no-hole case.
    """
    rings = (
        f"list_transform(range(1, ST_NumInteriorRings({poly}) + 1), "
        f"i -> ST_InteriorRingN({poly}, i))"
    )
    keep = f"ring -> ST_Area({zone_utm('ST_MakePolygon(ring)')}) >= {MIN_HOLE_M2}"
    rebuilt = f"ST_MakePolygon(ST_ExteriorRing({poly}), list_filter({rings}, {keep}))"
    return f"CASE WHEN ST_NumInteriorRings({poly}) = 0 THEN {poly} ELSE {rebuilt} END"


def final_select(rows: str, cid: str, year: int, bbox: list[float], max_m2: float) -> str:
    """Final columns + hk from rows (id, tile_key, g, pf_mean), unsorted.

    Both size filters, both metrics and the bbox are derived from the SAME final
    geometry, **after** the seam union. Filtering earlier deleted real fields: a
    field cut by a tile seam into two sub-``MIN_PARCEL_M2`` halves never reached
    ST_Union_Agg and vanished from the release, while a cap applied to merge's
    pre-union pixel area let a 6.4 km2 union through a 5 km2 cap. A bbox copied
    from the source row likewise disagreed with a geometry that ST_MakeValid and
    the part filter had since changed. Holes under ``MIN_HOLE_M2`` are filled in the
    same pass, after the union, so a seam-cut field's halves cannot leave one behind.
    """
    score = "CAST(LEAST(100, GREATEST(0, ROUND(pf_mean * 100))) AS UTINYINT)"
    det = (
        f"TIMESTAMPTZ '{year}-01-01 00:00:00+00' AS \"determination:datetime\", "
        f"'{DET_METHOD}' AS \"determination:method\""
    )
    # Branch on the geometry TYPE, not the part count: a one-part MULTIPOLYGON has
    # ST_NumGeometries = 1 but is not a Polygon, so the ring functions in
    # ``drop_small_holes`` return NULL for it and the row would vanish at the
    # ``g IS NOT NULL`` filter below. Every MULTIPOLYGON is dumped, however many parts.
    parts = (
        f"SELECT id, tile_key, pf_mean, CASE WHEN ST_GeometryType(g) = 'MULTIPOLYGON' "
        f"THEN ST_Collect(list_transform(list_filter(ST_Dump(g), "
        f"x -> ST_Area({zone_utm('x.geom')}) >= {MIN_PART_M2}), "
        f"x -> {drop_small_holes('x.geom')})) "
        f"ELSE {drop_small_holes('g')} END AS g FROM ({rows})"
    )
    metric = (
        f"SELECT id, pf_mean, g, {zone_utm('g')} AS gu FROM ({parts}) "
        f"WHERE g IS NOT NULL AND NOT ST_IsEmpty(g)"
    )
    sized = (
        f"SELECT id, pf_mean, g, ST_Area(gu) AS area, ST_Perimeter(gu) AS perim, "
        f"ST_XMin(g) AS xmin, ST_YMin(g) AS ymin, ST_XMax(g) AS xmax, ST_YMax(g) AS ymax "
        f"FROM ({metric}) WHERE ST_Area(gu) >= {MIN_PARCEL_M2} AND ST_Area(gu) <= {max_m2}"
    )
    return (
        f"SELECT id, '{cid}' AS collection, ST_AsWKB(g) AS geometry, "
        f"struct_pack(xmin := xmin, ymin := ymin, xmax := xmax, ymax := ymax) AS bbox, "
        f'area::FLOAT AS "metrics:area", perim::FLOAT AS "metrics:perimeter", {score} AS score, '
        f"{det}, {hk_expr(bbox)} AS hk FROM ({sized})"
    )


def strip_rows_sql() -> str:
    "Parcels near tile joins after the seam union (needs the join_seams temp tables)."
    merged = (
        "SELECT m.gid AS id, arg_min(c.tile_key, c.id) AS tile_key, "
        "ST_CollectionExtract(ST_MakeValid(ST_Union_Agg(c.g)), 3) AS g, "
        "coalesce(SUM(c.pf_mean * c.area) / nullif(SUM(c.area), 0), avg(c.pf_mean)) AS pf_mean "
        "FROM cand c JOIN groups m ON c.id = m.id GROUP BY m.gid"
    )
    return (
        "SELECT id, tile_key, g, pf_mean FROM cand WHERE id NOT IN (SELECT id FROM groups) "
        f"UNION ALL SELECT id, tile_key, g, pf_mean FROM ({merged})"
    )


def write_sorted(
    reader: Iterable,
    schema: pa.Schema,
    dst: Path,
    row_group: int,
    validate: Callable[[Path], None] | None = None,
) -> int:
    "Stream Arrow batches into ``row_group``-row groups (zstd 19, page index) at ``dst``."
    tmp = dst.with_name(dst.name + f".tmp-{os.getpid()}")
    n = 0
    buf: list[pa.Table] = []
    buffered = 0
    try:
        with pq.ParquetWriter(
            tmp, schema, compression="zstd", compression_level=19, write_page_index=True
        ) as w:
            for batch in reader:
                tbl = pa.Table.from_batches([batch]).select(schema.names).cast(schema)
                buf.append(tbl)
                buffered += tbl.num_rows
                n += tbl.num_rows
                if buffered >= row_group:
                    whole = pa.concat_tables(buf)
                    full = whole.num_rows // row_group * row_group
                    w.write_table(whole.slice(0, full), row_group_size=row_group)
                    buf, buffered = [whole.slice(full)], whole.num_rows - full
            if buffered:
                w.write_table(pa.concat_tables(buf), row_group_size=row_group)
        if validate is not None:
            validate(tmp)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return n
