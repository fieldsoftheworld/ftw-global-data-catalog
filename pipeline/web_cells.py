#!/usr/bin/env python3
"""Multi-resolution A5 cell aggregates for the viewer's overview layer (``cells-{year}.pmtiles``).

One scan of a year's merged parcel parquet (``global{year}_gp2.parquet``) puts every parcel
centroid in memory, and each of the finer resolutions (r7 to r11 by default, up to r12) is bucketed directly
from the centroids with ``a5_lonlat_to_cell``, exactly as ``gpio process aggregate a5
--resolution R`` does, so r7 reproduces the published ``cells_a5r7_{year}.parquet`` (checked with
``--check-r7``). The coarser resolutions (r4 to r6, cells of 90 km and up) are bucketed from the
centres of the r7 cells: A5's hierarchy is not geometrically nested (a random point's r7 cell is
its r10 cell's ``a5_cell_to_parent`` only 65% of the time), so parent ids would assign boundary
parcels to the wrong polygon, while a 45 km cell's centre sits well inside one coarse cell.

Outputs in ``--out``, per resolution R (columns as the published r7 cells, plus ``density``, fields
per 1,000 km2, whose range stays the same at every resolution where a plain count shrinks 4x per level:
``a5_cell, count, density, area_ha, avg_score, pct_covered, geometry``):

    cells_a5r{R}_{year}_plain.parquet   plain parquet, WKB polygon per cell (CRS84)

``web_cells.sbatch`` then converts them to GeoParquet 2.0 (``cells_a5r{R}_{year}.parquet``), tiles
each into one zoom level (tile z = R - ZOFF) with tylertoo and merges the tiles into
``cells-{year}.pmtiles``. The published files use r4 to r11 at z0 to z7 (the defaults here and in
``web_cells.sbatch``), so a hexagon is 5-9 px wide at every zoom.

Needs the gpio venv (geoparquet-io, duckdb and its a5 community extension):
    ~/ftw-us-tiles/venv/bin/python pipeline/web_cells.py 2025 --in global2025_gp2.parquet --out cells --check-r7 cells_a5r7_2025.parquet
"""

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb

EARTH_KM2 = 510_065_621.724  # km2; A5 cells are equal-area, 60 * 4**(R-1) of them for R >= 1
MAX_FINEST = 12  # r12 cells are ~2 km2 (1.5 km wide); r12 of the globe has up to 16x the r10 cells
DIRECT_FROM = 7  # resolutions at least this fine are bucketed from the parcel centroids


def cell_km2(res: int) -> float:
    """Area of one A5 cell. r7 = 2,075.5 km2, matching the published r7 cells."""
    return EARTH_KM2 / (60 * 4 ** (res - 1))


def load_points(con, src: str, limit: int | None) -> None:
    """Temp table `pts`: one row per parcel with its centroid, area (m2) and score."""
    lim = f" LIMIT {limit}" if limit else ""
    t0 = time.time()
    con.execute(
        f"""
        CREATE TEMP TABLE pts AS
        SELECT ST_X(c) AS lon, ST_Y(c) AS lat, area, score
        FROM (
            SELECT ST_Centroid(geometry) AS c, "metrics:area" AS area, score
            FROM read_parquet('{src}'){lim}
        )
        WHERE c IS NOT NULL
        """
    )
    n = con.execute("SELECT count(*) FROM pts").fetchone()[0]
    print(f"{n:,} parcel centroids in {time.time() - t0:.0f} s", flush=True)


def bucket_points(con, res: int) -> None:
    """Temp table `agg{res}`: the parcels bucketed in A5 cells of this resolution."""
    t0 = time.time()
    con.execute(
        f"""
        CREATE TEMP TABLE agg{res} AS
        SELECT a5_lonlat_to_cell(lon, lat, {res}) AS a5_cell,
               count(*) AS n, sum(area) AS area_m2, sum(score) AS score_sum
        FROM pts GROUP BY 1
        """
    )
    n = con.execute(f"SELECT count(*) FROM agg{res}").fetchone()[0]
    print(f"r{res}: {n:,} cells from the parcel centroids in {time.time() - t0:.0f} s", flush=True)


def bucket_cells(con, res: int, fine: int) -> None:
    """Temp table `agg{res}`: the cells of `agg{fine}` bucketed by their centres."""
    con.execute(
        f"""
        CREATE TEMP TABLE agg{res} AS
        SELECT a5_lonlat_to_cell(c[1], c[2], {res}) AS a5_cell,
               sum(n) AS n, sum(area_m2) AS area_m2, sum(score_sum) AS score_sum
        FROM (SELECT a5_cell_to_lonlat(a5_cell) AS c, n, area_m2, score_sum FROM agg{fine})
        GROUP BY 1
        """
    )
    n = con.execute(f"SELECT count(*) FROM agg{res}").fetchone()[0]
    print(f"r{res}: {n:,} cells from the centres of the r{fine} cells", flush=True)


def final_sql(res: int) -> str:
    return f"""
        SELECT a5_cell,
               CAST(sum(n) AS BIGINT) AS count,
               round(sum(n) / {cell_km2(res)!r} * 1000, 1) AS density,
               CAST(round(sum(area_m2) / 1e4) AS BIGINT) AS area_ha,
               round(sum(score_sum) / sum(n), 1) AS avg_score,
               round(100 * sum(area_m2) / {cell_km2(res) * 1e6!r}, 1) AS pct_covered
        FROM agg{res} GROUP BY 1
    """


def write_res(con, res: int, out: Path, year: int) -> Path:
    from geoparquet_io.core.process.aggregate.by_a5 import A5_SCHEME
    from geoparquet_io.core.process.aggregate.grid_common import wrap_grid_geometry

    plain = out / f"cells_a5r{res}_{year}_plain.parquet"
    sql = wrap_grid_geometry(final_sql(res), A5_SCHEME, "a5_cell", "polygon")
    con.execute(
        f"COPY (SELECT * EXCLUDE (geometry), ST_GeomFromWKB(geometry) AS geometry FROM ({sql}) ORDER BY a5_cell) TO '{plain}' "
        "(FORMAT PARQUET, COMPRESSION zstd, ROW_GROUP_SIZE 65536)"
    )
    return plain


def check_r7(con, ref: str, out: Path, year: int) -> bool:
    got = out / f"cells_a5r7_{year}_plain.parquet"
    row = con.execute(
        f"""
        SELECT count(*) AS cells,
               sum(CASE WHEN r.count IS NULL OR r.count <> g.count THEN 1 ELSE 0 END) AS count_diff,
               max(abs(r.area_ha - g.area_ha)) AS max_area_diff_ha,
               max(abs(r.avg_score - g.avg_score)) AS max_score_diff,
               max(abs(r.pct_covered - g.pct_covered)) AS max_pct_diff
        FROM '{got}' g LEFT JOIN '{ref}' r USING (a5_cell)
        """
    ).fetchone()
    nref = con.execute(f"SELECT count(*) FROM '{ref}'").fetchone()[0]
    print(
        f"check r7 vs {ref}: cells {row[0]:,} (ref {nref:,}), count mismatches {row[1]}, "
        f"max area diff {row[2]} ha, max score diff {row[3]}, max pct_covered diff {row[4]}",
        flush=True,
    )
    return (
        row[0] == nref and row[1] == 0 and (row[4] or 0) <= 0.11
    )  # the published pct uses a measured 2,075.5 km2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("year", type=int)
    ap.add_argument(
        "--in", dest="src", required=True, help="merged parcel parquet (global{year}_gp2.parquet)"
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--finest", type=int, default=11, help="finest resolution, at most 12")
    ap.add_argument("--coarsest", type=int, default=4)
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", "8")))
    ap.add_argument("--memory", default="14GB")
    ap.add_argument("--limit", type=int, help="first N rows only (smoke test)")
    ap.add_argument(
        "--check-r7", help="published cells_a5r7_{year}.parquet to compare the roll-up with"
    )
    a = ap.parse_args()
    if not a.coarsest <= a.finest <= MAX_FINEST:
        ap.error(f"need coarsest <= finest <= {MAX_FINEST}")

    a.out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    from geoparquet_io.core.duckdb_utils import load_community_extension

    con.execute("INSTALL spatial; LOAD spatial")
    load_community_extension(con, "a5")
    con.execute(
        f"SET threads={a.threads}; SET memory_limit='{a.memory}'; SET preserve_insertion_order=false"
    )
    con.execute(f"SET temp_directory='{a.out}/duck-tmp-{a.year}'")

    load_points(con, a.src, a.limit)
    direct = [r for r in range(a.finest, DIRECT_FROM - 1, -1) if r >= a.coarsest]
    for res in direct:
        bucket_points(con, res)
    for res in range(min(direct) - 1, a.coarsest - 1, -1):
        bucket_cells(con, res, min(direct))
    for res in range(a.coarsest, a.finest + 1):
        path = write_res(con, res, a.out, a.year)
        n = con.execute(f"SELECT count(*) FROM '{path}'").fetchone()[0]
        print(f"r{res}: {n:,} cells -> {path.name}", flush=True)
    ok = True
    if a.check_r7 and not a.limit and DIRECT_FROM in direct:
        ok = check_r7(con, a.check_r7, a.out, a.year)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
