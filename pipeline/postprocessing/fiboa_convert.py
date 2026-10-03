"Repair seams and write fiboa GeoParquet by UTM zone."

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS

sys.path.insert(0, str(Path(__file__).parent))
from fiboa_common import (
    MIN_HOLE_M2,
    MIN_PART_M2,
    connect,
    discover_zones,
    final_select,
    strip_rows_sql,
    write_sorted,
    zone_at,
    zone_utm,
)

IN_ROOT = Path("merged")
OUT_ROOT = Path("fiboa")
TMP_ROOT = Path("scratch/duckdb")
ROW_GROUP = 8192
VECOREL = "https://vecorel.org/specification/v0.1.0/schema.yaml"
FIBOA = "https://fiboa.org/specification/v0.3.0/schema.yaml"
SDL = "https://vecorel.org/sdl/v0.2.0/schema.json"


SCORE_DESC = "Mean model field probability inside the parcel, x 100 rounded (0-100)"
GEOMETRY_METRICS = "https://vecorel.org/geometry-metrics-extension/v0.1.0/schema.yaml"

#: Band order PR1 stacks and PR2's run.py stamps as ``input_bands``.
INPUT_BANDS = "B04/B03/B02/B08"
#: Fallback only; the real spec is ``outlines.SPEC`` and reaches us via _summary.json.
DEFAULT_SPEC = "nbg-pb-h0.01-t0.3+R35+F10+G2+A900"
#: Fallback parcel-area cap when merge's ``_summary.json`` is absent, m2.
MAX_PARCEL_M2 = 5.0e6


def require_geo_metadata(src: Path) -> None:
    """Fail early if the GeoParquet ``geo`` footer was stripped from ``src``.

    DuckDB only auto-converts the BLOB ``geometry`` column to GEOMETRY because of
    that footer. Without it ``merge_polygons``' ST_Hilbert(ST_Centroid(...)) and
    this module's ST_MakeValid both die with a bare "Binder Error: No function
    matches 'ST_Centroid(BLOB)'" that says nothing about the real cause.
    """
    md = pq.ParquetFile(src).schema_arrow.metadata or {}
    if b"geo" not in md:
        raise SystemExit(
            f"{src}: no GeoParquet 'geo' footer -- DuckDB cannot read the geometry column "
            "as GEOMETRY. Whoever wrote this file dropped the schema metadata."
        )


def collection_metadata(cid: str, year: int, summary: dict | None = None) -> dict:
    "Collection metadata; the numbers come from merge's ``_summary.json``, not literals."
    s = summary or {}
    max_km2 = s.get("max_km2", MAX_PARCEL_M2 / 1e6)
    tol = s.get("simplify_tolerance_m")
    simplify = f"{tol:g} m coverage simplification, " if tol else ""
    partial = ""
    if s.get("allow_missing"):
        n = sum(len(v) for v in (s.get("missing") or {}).values())
        partial = f" INCOMPLETE: built with --allow-missing over {n} missing input tiles."
    return {
        "schemas": {cid: [VECOREL, GEOMETRY_METRICS, FIBOA]},
        "collection": cid,
        "determination:details": (
            "Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics "
            f"(CDSE sentinel-2-global-mosaics, {year} Q1-Q4, 4 quarters x {INPUT_BANDS}), "
            "2.5 m field/boundary probabilities, BoundaryVote instance post-processing "
            f"({s.get('spec', DEFAULT_SPEC)}), {simplify}parcels > {max_km2:g} km2 removed, "
            f"parcels and parts under {MIN_PART_M2:g} m2 removed, "
            f"interior holes under {MIN_HOLE_M2:g} m2 filled. "
            "Attributes are for filtering; no land-cover masking was applied." + partial
        ),
        "schemas:custom": {
            "$schema": SDL,
            "properties": {"score": {"type": "uint8", "description": SCORE_DESC}},
        },
    }


def geo_metadata(bbox: list[float]) -> dict:
    return {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Polygon", "MultiPolygon"],
                "crs": json.loads(CRS("EPSG:4326").to_json()),
                "bbox": bbox,
                "covering": {"bbox": {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}},
            }
        },
    }


def arrow_schema(cid: str) -> pa.Schema:
    fields = [
        pa.field("id", pa.string(), nullable=False),
        pa.field("collection", pa.string(), nullable=False),
        pa.field("geometry", pa.binary(), nullable=False),
        pa.field(
            "bbox",
            pa.struct([pa.field(k, pa.float64()) for k in ("xmin", "ymin", "xmax", "ymax")]),
        ),
        pa.field("metrics:area", pa.float32()),
        pa.field("metrics:perimeter", pa.float32()),
    ]
    fields += [
        pa.field("score", pa.uint8()),
        pa.field("determination:datetime", pa.timestamp("ms", tz="UTC")),
        pa.field("determination:method", pa.string()),
    ]
    return pa.schema(fields)


JOIN_MIN_OVERLAP_M2 = 100.0
WINDOW_MIN_OVERLAP_FRAC = 0.1
STRIP_PAD_DEG = 0.0005
GRID_DEG = 0.01

#: Two pieces are the same field seen twice only if they genuinely OVERLAP.
#:
#: The cross-tile branch used to accept a bare shared edge of JOIN_MIN_EDGE_M = 20 m
#: with no overlap at all, but a shared edge is *adjacency*, not duplication: two
#: distinct neighbouring fields that merely touch along a tile seam were fused into
#: one parcel with a blended score (measured: two 4,668 m2 fields touching along
#: x = -3.0 published as one 9,336 m2 feature at score 70, while the identical pair
#: inside a single tile correctly published two). Seams run for tens of thousands of
#: km, so that bar produced false merges at scale. Both branches now require the
#: same positive fractional overlap; cross-tile additionally needs an absolute
#: floor so two slivers cannot pair up.
MIN_OVERLAP = (
    f"ST_Area(x) >= {WINDOW_MIN_OVERLAP_FRAC} * amin "
    f"AND (NOT xt OR ST_Area(x) >= {JOIN_MIN_OVERLAP_M2})"
)


IN_STRIP = (
    "(EXISTS (SELECT 1 FROM strips s "
    "WHERE xmin <= s.x1 AND xmax >= s.x0 AND ymin <= s.y1 AND ymax >= s.y0) "
    "OR EXISTS (SELECT 1 FROM twbox t WHERE t.tk = tile_key "
    "AND xmin <= t.x1 AND xmax >= t.x0 AND ymin <= t.y1 AND ymax >= t.y0))"
)


def repaired(src: Path, where: str) -> str:
    """Validity-repaired parcels of the zone file matching ``where``.

    Repair only: every size filter now runs once, after the seam union, in
    ``fiboa_common.final_select``. The hemisphere split this used to carry is
    gone too -- see ``fiboa_common.zone_utm``.
    """
    return (
        f"SELECT tile_key || '-' || parcel_id AS id, tile_key, g, pf_mean "
        f"FROM (SELECT tile_key, parcel_id, pf_mean, "
        f"ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g "
        f"FROM read_parquet('{src}') WHERE {where}) "
        f"WHERE g IS NOT NULL AND NOT ST_IsEmpty(g)"
    )


def tile_overlaps(con: duckdb.DuckDBPyConnection, src: Path) -> list[tuple[float, ...]]:
    "Rectangles where two tiles' parcel extents overlap; cross-tile joins live inside them."
    ext = con.sql(
        f"SELECT tile_key, min(xmin), min(ymin), max(xmax), max(ymax) "
        f"FROM read_parquet('{src}') GROUP BY tile_key"
    ).fetchall()
    out = []
    for i, a in enumerate(ext):
        for b in ext[i + 1 :]:
            x0, y0, x1, y1 = max(a[1], b[1]), max(a[2], b[2]), min(a[3], b[3]), min(a[4], b[4])
            if x0 <= x1 and y0 <= y1:
                p = STRIP_PAD_DEG
                out.append((x0 - p, y0 - p, x1 + p, y1 + p))
    return out


def components(pairs: list[tuple[str, str]]) -> dict[str, str]:
    "Connected components of the join pairs: id -> smallest id in its component."
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {x: find(x) for x in list(parent)}


def join_seams(con: duckdb.DuckDBPyConnection, src: Path) -> int:
    "Build temp tables ``cand`` (repaired parcels near tile joins) and ``groups`` (id, gid)."
    rects = tile_overlaps(con, src)
    con.sql("CREATE TEMP TABLE strips (x0 DOUBLE, y0 DOUBLE, x1 DOUBLE, y1 DOUBLE)")
    if rects:
        con.executemany("INSERT INTO strips VALUES (?, ?, ?, ?)", rects)
    con.sql(
        f"CREATE TEMP TABLE twbox AS SELECT tile_key AS tk, tile_key || '-' || parcel_id AS id, "
        f"xmin AS x0, ymin AS y0, xmax AS x1, ymax AS y1 "
        f"FROM read_parquet('{src}') WHERE coalesce(touches_window_edge, false)"
    )
    # Bounds come from the repaired geometry, not the source row: the candidate grid
    # and the bbox prefilter below must agree with the geometry actually compared.
    con.sql(
        f"CREATE TEMP TABLE cand AS SELECT *, ST_Area(gu) AS area FROM ("
        f"SELECT id, tile_key, g, pf_mean, ST_XMin(g) AS xmin, ST_YMin(g) AS ymin, "
        f"ST_XMax(g) AS xmax, ST_YMax(g) AS ymax, ST_MakeValid({zone_utm('g')}) AS gu, "
        f"id IN (SELECT id FROM twbox) AS twe FROM ({repaired(src, IN_STRIP)}))"
    )
    con.sql(
        f"CREATE TEMP TABLE cells AS SELECT id, cx, unnest(generate_series(y0, y1)) AS cy FROM ("
        f"SELECT id, y0, y1, unnest(generate_series(x0, x1)) AS cx FROM ("
        f"SELECT id, CAST(floor(xmin / {GRID_DEG}) AS BIGINT) AS x0, CAST(floor(xmax / {GRID_DEG}) AS BIGINT) AS x1, "
        f"CAST(floor(ymin / {GRID_DEG}) AS BIGINT) AS y0, CAST(floor(ymax / {GRID_DEG}) AS BIGINT) AS y1 FROM cand))"
    )
    pairs = con.sql(
        f"SELECT ida, idb FROM ("
        f"SELECT a.id AS ida, b.id AS idb, a.tile_key <> b.tile_key AS xt, "
        f"least(a.area, b.area) AS amin, ST_Intersection(a.gu, b.gu) AS x "
        f"FROM cells ca JOIN cells cb ON ca.cx = cb.cx AND ca.cy = cb.cy AND ca.id < cb.id "
        f"JOIN cand a ON a.id = ca.id JOIN cand b ON b.id = cb.id "
        f"WHERE (a.tile_key <> b.tile_key OR a.twe OR b.twe) "
        f"AND a.xmin <= b.xmax AND a.xmax >= b.xmin AND a.ymin <= b.ymax AND a.ymax >= b.ymin "
        f"AND ca.cx = CAST(floor(greatest(a.xmin, b.xmin) / {GRID_DEG}) AS BIGINT) "
        f"AND ca.cy = CAST(floor(greatest(a.ymin, b.ymin) / {GRID_DEG}) AS BIGINT) "
        f"AND ST_Intersects(a.gu, b.gu)) "
        f"WHERE {MIN_OVERLAP}"
    ).fetchall()
    comp = components(pairs)
    tbl = pa.table(
        {"id": list(comp), "gid": list(comp.values())},
        schema=pa.schema([pa.field("id", pa.string()), pa.field("gid", pa.string())]),
    )
    con.register("groups_arrow", tbl)
    con.sql("CREATE TEMP TABLE groups AS SELECT * FROM groups_arrow")
    n_groups = len(set(comp.values()))
    print(
        f"cross-tile joins: {len(rects)} overlap strips, {len(pairs)} pairs, "
        f"{len(comp)} pieces -> {n_groups} parcels",
        flush=True,
    )
    return len(comp) - n_groups


def query(src: Path, cid: str, year: int, bbox: list[float], max_m2: float) -> str:
    "One SELECT sorted by (Hilbert index of the bbox centre, id); needs ``join_seams`` tables."
    cols = "id, tile_key, g, pf_mean"
    rows = f"SELECT {cols} FROM ({repaired(src, 'NOT ' + IN_STRIP)}) UNION ALL {strip_rows_sql()}"
    return f"{final_select(rows, cid, year, bbox, max_m2)} ORDER BY hk, id"


def merge_summary(year: int) -> dict:
    "merge_polygons' ``_summary.json``, which records what the inputs actually are."
    p = IN_ROOT / str(year) / "_summary.json"
    if not p.is_file():
        print(f"no {p}: falling back to documented defaults for provenance", flush=True)
        return {}
    return json.loads(p.read_text())


def convert(year: int, zone: str, threads: int, memory_limit: str, out_root: Path) -> Path:
    cid = f"ftw-s2-{year}"
    src = IN_ROOT / str(year) / f"zone={zone}" / "part-0.parquet"
    # Hive layout: the published catalog documents vector/{year}/zone=NN/utm{NN}.parquet
    # and every collection declares "partition:glob": "./zone=*/utm*.parquet".
    dst = out_root / str(year) / f"zone={zone}" / f"utm{zone}.parquet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    summary = merge_summary(year)
    max_m2 = float(summary.get("max_km2", MAX_PARCEL_M2 / 1e6)) * 1e6
    tmp_dir = TMP_ROOT / f"{year}-{zone}-{os.getpid()}"
    con = connect(threads, memory_limit, tmp_dir)
    try:
        require_geo_metadata(src)
        row = con.sql(
            f"select min(xmin), min(ymin), max(xmax), max(ymax), count(*) "
            f"from read_parquet('{src}')"
        ).fetchone()
        if row is None or row[4] == 0 or any(v is None for v in row[:4]):
            # An all-NULL aggregate is a row, so the old `assert bbox is not None`
            # could not catch it and float(None) raised naming nothing.
            raise SystemExit(f"zone {zone} ({year}): {src} has no usable rows ({row})")
        bbox = [float(v) for v in row[:4]]
        n_in = int(row[4])
        join_seams(con, src)
        schema = arrow_schema(cid).with_metadata(
            {
                b"collection": json.dumps(collection_metadata(cid, year, summary)).encode(),
                b"geo": json.dumps(geo_metadata(bbox)).encode(),
            }
        )
        reader = con.sql(query(src, cid, year, bbox, max_m2)).to_arrow_reader(
            batch_size=ROW_GROUP
        )
        n = write_sorted(reader, schema, dst, ROW_GROUP, validate=_validator(zone, n_in))
    finally:
        con.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"{dst.name}: {n:,} features from {n_in:,} rows, {dst.stat().st_size / 1e9:.2f} GB")
    return dst


def _validator(zone: str, n_in: int):
    """Check the staged file before it is published; ``write_sorted`` discards it on raise.

    The seam union and the size filters only ever reduce the row count, so more
    rows out than in means the inputs fanned out (a duplicated ``(tile_key,
    parcel_id)`` key, for instance) and the published parcel ids are not unique --
    which ``catalog/vector/{year}/AGENTS.md`` promises they are.
    """

    def check(tmp: Path) -> None:
        md = pq.ParquetFile(tmp).metadata
        if md.num_rows > n_in:
            raise SystemExit(
                f"zone {zone}: {md.num_rows:,} rows written from {n_in:,} input rows -- "
                "the inputs fanned out; parcel ids are not unique"
            )
        # read_parquet explicitly: the staged file is named *.tmp-<pid>, so DuckDB
        # cannot infer the reader from its suffix.
        ids = duckdb.sql(
            f"select count(*), count(distinct id) from read_parquet('{tmp}')"
        ).fetchone()
        assert ids is not None
        if ids[0] != ids[1]:
            raise SystemExit(f"zone {zone}: {ids[0] - ids[1]:,} duplicate parcel ids")

    return check


def main() -> None:
    global IN_ROOT, TMP_ROOT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-root", type=Path, default=IN_ROOT)
    ap.add_argument("--tmp-dir", type=Path, default=TMP_ROOT)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--zone", help="UTM zone, e.g. 15")
    ap.add_argument("--zone-index", type=int, help="index into the year's sorted zone list")
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", "4")))
    ap.add_argument("--memory-limit", default="12GB", help="DuckDB memory limit (spills beyond)")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT)
    a = ap.parse_args()
    IN_ROOT, TMP_ROOT = a.in_root, a.tmp_dir
    zones = discover_zones(IN_ROOT, a.year)
    if a.zone is None and a.zone_index is None:
        sys.exit(f"pass --zone or --zone-index (0-{len(zones) - 1})")
    zone = a.zone or zone_at(zones, a.zone_index)
    convert(a.year, zone, a.threads, a.memory_limit, a.out_root)


if __name__ == "__main__":
    main()
