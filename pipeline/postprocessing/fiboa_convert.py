"Repair seams and write fiboa GeoParquet by UTM zone."

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
from pyproj import CRS

sys.path.insert(0, str(Path(__file__).parent))
from fiboa_common import (
    connect,
    discover_zones,
    final_select,
    strip_rows_sql,
    utm_of,
    write_sorted,
    zone_at,
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


def collection_metadata(cid: str, year: int) -> dict:
    return {
        "schemas": {cid: [VECOREL, GEOMETRY_METRICS, FIBOA]},
        "collection": cid,
        "determination:details": (
            "Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics "
            f"(CDSE sentinel-2-global-mosaics, {year} Q1-Q4, 4 quarters x B02/B03/B04/B08), "
            "2.5 m field/boundary probabilities, BoundaryVote instance post-processing "
            "(nbg-pb-h0.01-t0.3+R35+F10+G2+A900), 5 m coverage simplification, parcels > 5 km2 removed. "
            "Attributes are for filtering; no land-cover masking was applied."
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
JOIN_MIN_EDGE_M = 20.0
WINDOW_MIN_OVERLAP_FRAC = 0.1
STRIP_PAD_DEG = 0.0005
GRID_DEG = 0.01


IN_STRIP = (
    "(EXISTS (SELECT 1 FROM strips s "
    "WHERE xmin <= s.x1 AND xmax >= s.x0 AND ymin <= s.y1 AND ymax >= s.y0) "
    "OR EXISTS (SELECT 1 FROM twbox t WHERE t.tk = tile_key "
    "AND xmin <= t.x1 AND xmax >= t.x0 AND ymin <= t.y1 AND ymax >= t.y0))"
)


def repaired(src: Path, where: str) -> str:
    "Repaired, filtered parcels of the zone file matching ``where`` (raw source columns)."
    halves = []
    for hemi, cond in (("326", "tile_key[3] >= 'N'"), ("327", "tile_key[3] < 'N'")):
        utm = f"ST_Transform(g, 'EPSG:4326', 'EPSG:{hemi}' || tile_key[1:2], true)"
        halves.append(
            f"SELECT tile_key || '-' || parcel_id AS id, tile_key, '{hemi}' AS hemi, g, "
            f"xmin, ymin, xmax, ymax, pf_mean, ST_Area({utm}) AS area, "
            f"ST_Perimeter({utm}) AS perim "
            f"FROM (SELECT *, CASE WHEN ST_NumGeometries(g0) > 1 THEN "
            f"ST_Collect(list_transform(list_filter(ST_Dump(g0), "
            f"x -> ST_Area(ST_Transform(x.geom, 'EPSG:4326', 'EPSG:{hemi}' || tile_key[1:2], true)) >= 900), "
            f"x -> x.geom)) ELSE g0 END AS g "
            f"FROM (SELECT *, ST_CollectionExtract(ST_MakeValid(geometry), 3) AS g0 "
            f"FROM read_parquet('{src}') WHERE {cond} AND {where})) "
            f"WHERE ST_Area({utm}) >= 900"
        )
    return " UNION ALL ".join(halves)


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
    con.sql(
        f"CREATE TEMP TABLE cand AS SELECT *, ST_MakeValid({utm_of('g')}) AS gu, "
        f"id IN (SELECT id FROM twbox) AS twe FROM ({repaired(src, IN_STRIP)})"
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
        f"WHERE (xt AND (ST_Area(x) >= {JOIN_MIN_OVERLAP_M2} "
        f"OR ST_Length(ST_CollectionExtract(x, 2)) >= {JOIN_MIN_EDGE_M})) "
        f"OR (NOT xt AND ST_Area(x) >= {WINDOW_MIN_OVERLAP_FRAC} * amin)"
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


def query(src: Path, cid: str, year: int, bbox: list[float]) -> str:
    "One SELECT sorted by (Hilbert index of the bbox centre, id); needs ``join_seams`` tables."
    cols = "id, g, xmin, ymin, xmax, ymax, pf_mean, area, perim"
    rows = f"SELECT {cols} FROM ({repaired(src, 'NOT ' + IN_STRIP)}) UNION ALL {strip_rows_sql()}"
    return f"{final_select(rows, cid, year, bbox)} ORDER BY hk, id"


def convert(year: int, zone: str, threads: int, memory_limit: str, out_root: Path) -> Path:
    cid = f"ftw-s2-{year}"
    src = IN_ROOT / str(year) / f"zone={zone}" / "part-0.parquet"
    dst = out_root / str(year) / f"utm{zone}.parquet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = TMP_ROOT / f"{year}-{zone}-{os.getpid()}"
    con = connect(threads, memory_limit, tmp_dir)
    bbox = con.sql(
        f"select min(xmin), min(ymin), max(xmax), max(ymax) from read_parquet('{src}')"
    ).fetchone()
    assert bbox is not None
    bbox = [float(v) for v in bbox]
    join_seams(con, src)
    schema = arrow_schema(cid).with_metadata(
        {
            b"collection": json.dumps(collection_metadata(cid, year)).encode(),
            b"geo": json.dumps(geo_metadata(bbox)).encode(),
        }
    )
    reader = con.sql(query(src, cid, year, bbox)).to_arrow_reader(batch_size=ROW_GROUP)
    n = write_sorted(reader, schema, dst, ROW_GROUP)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"{dst.name}: {n:,} features, {dst.stat().st_size / 1e9:.2f} GB", flush=True)
    return dst


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
