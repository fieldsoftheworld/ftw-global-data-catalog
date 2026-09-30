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


def utm_of(geom: str) -> str:
    return f"ST_Transform({geom}, 'EPSG:4326', 'EPSG:' || hemi || tile_key[1:2], true)"


def hk_expr(bbox: list[float]) -> str:
    "Hilbert index of the bbox centre on a square curve so cells stay square in degrees."
    w, s, e, n = bbox
    side = max(e - w, n - s, 1e-6)
    curve = f"{{min_x: {w}, min_y: {s}, max_x: {w + side}, max_y: {s + side}}}::BOX_2D"
    return f"ST_Hilbert((xmin + xmax) / 2, (ymin + ymax) / 2, {curve})"


def final_select(rows: str, cid: str, year: int, bbox: list[float]) -> str:
    "Final columns + hk from rows (id, g, xmin..ymax, pf_mean, area, perim), unsorted."
    score = "CAST(LEAST(100, GREATEST(0, ROUND(pf_mean * 100))) AS UTINYINT)"
    det = (
        f"TIMESTAMPTZ '{year}-01-01 00:00:00+00' AS \"determination:datetime\", "
        f"'{DET_METHOD}' AS \"determination:method\""
    )
    return (
        f"SELECT id, '{cid}' AS collection, ST_AsWKB(g) AS geometry, "
        f"struct_pack(xmin := xmin, ymin := ymin, xmax := xmax, ymax := ymax) AS bbox, "
        f'area::FLOAT AS "metrics:area", perim::FLOAT AS "metrics:perimeter", {score} AS score, '
        f"{det}, {hk_expr(bbox)} AS hk FROM ({rows})"
    )


def strip_rows_sql() -> str:
    "Parcels near tile joins after the seam union (needs the join_seams temp tables)."
    cols = "id, g, xmin, ymin, xmax, ymax, pf_mean, area, perim"
    merged = (
        "SELECT m.gid AS id, arg_min(c.tile_key, c.id) AS tile_key, arg_min(c.hemi, c.id) AS hemi, "
        "ST_CollectionExtract(ST_MakeValid(ST_Union_Agg(c.g)), 3) AS g, "
        "SUM(c.pf_mean * c.area) / SUM(c.area) AS pf_mean "
        "FROM cand c JOIN groups m ON c.id = m.id GROUP BY m.gid"
    )
    return (
        f"SELECT {cols} FROM cand WHERE id NOT IN (SELECT id FROM groups) "
        f"UNION ALL SELECT id, g, ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g), pf_mean, "
        f"ST_Area({utm_of('g')}), ST_Perimeter({utm_of('g')}) FROM ({merged})"
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
