"""Vector QA of one year's fiboa zone files, optionally against a release and another year.

Checks:
- totals: parcels and km2 per zone and overall, and the zones that moved by more than
  ``--zone-tol`` against each reference
- cap: the largest ``metrics:area`` and the rows over ``--max-km2`` (none expected)
- ids: duplicate ids across all zone files (none expected)
- strips: parcels whose bbox centre lies in the orphan strips the claim rule fills (south-west
  Norway west of 6E, Netherlands 6.00-6.04E, Iowa/Wisconsin east of -90, Thailand east of
  102E, Sulawesi east of 120E)
- cross-zone: parcels of neighbouring zone files near the zone line that overlap by at least
  half of the smaller one, i.e. one field kept by two zones (none expected)
- equator: overlapping pairs from tiles on opposite sides of the equator
- sea (``--land-polygons``): share of a per-zone sample with less than half its area on land
- north edge (``--footprints``): per tile, parcels centred in the top 1 km of the tile raster
  (west 61 km) against the 1.5-4.5 km band below; tiles with a pile-up (> 3x) or a gap (< 1/3)
  and at least 30 parcels on the dense side

    python qa_vector.py 2025 --root fiboa --out qa/vector_2025.json \\
        [--release release-fiboa] [--prev-year 2024 --prev-root fiboa] \\
        [--land-polygons land.parquet] [--footprints tile_footprints.parquet]
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).parent))
from sea_filter import SeaFilter

STRIPS = {  # name: (lon0, lon1, lat0, lat1)
    "norway_w6e": (4.5, 6.0, 58.0, 61.5),
    "nl_6e": (6.0, 6.04, 51.8, 53.6),
    "iowa_wi_90w": (-90.0, -89.6, 41.0, 47.0),
    "thailand_102e": (102.0, 102.45, 12.0, 19.0),
    "sulawesi_120e": (120.0, 120.45, -6.0, 2.0),
}


def files(root: Path, year: int) -> list[Path]:
    return sorted((root / str(year)).glob("zone=*/utm*.parquet"))


def glob(root: Path, year: int) -> str:
    return f"{root}/{year}/zone=*/utm*.parquet"


def one(c: duckdb.DuckDBPyConnection, sql: str):
    return c.sql(sql).fetchall()[0][0]


def totals(c: duckdb.DuckDBPyConnection, root: Path, year: int, cap_m2: float) -> dict[str, Any]:
    rows = c.sql(
        f'select zone, count(*) n, sum("metrics:area") / 1e6 km2, max("metrics:area") amax, '
        f'count(*) filter (where "metrics:area" > {cap_m2 + 1}) over_cap '
        f"from read_parquet('{glob(root, year)}', hive_partitioning=true) "
        f"group by zone order by zone"
    ).fetchall()
    return {
        "n": sum(r[1] for r in rows),
        "km2": round(sum(r[2] for r in rows), 1),
        "max_area_m2": max(r[3] for r in rows),
        "over_cap": sum(r[4] for r in rows),
        "zone_files": len(rows),
        "zones": {str(z).zfill(2): {"n": n, "km2": round(k, 1)} for z, n, k, _, _ in rows},
    }


def dup_ids(c: duckdb.DuckDBPyConnection, root: Path, year: int) -> int:
    return one(c, f"select count(*) - count(distinct id) from read_parquet('{glob(root, year)}')")


def strips(c: duckdb.DuckDBPyConnection, root: Path, year: int) -> dict[str, int]:
    return {
        name: one(
            c,
            f"select count(*) from read_parquet('{glob(root, year)}') "
            f"where (bbox.xmin + bbox.xmax) / 2 between {x0} and {x1} "
            f"and (bbox.ymin + bbox.ymax) / 2 between {y0} and {y1}",
        )
        for name, (x0, x1, y0, y1) in STRIPS.items()
    }


def zone_lines() -> list[tuple[int, int, float, float, float]]:
    """(west zone, east zone, lon, lat0, lat1) of every zone line, with the band V exception."""
    lines = [(z, z + 1, -180 + 6 * z, -80.0, 84.0) for z in range(1, 60)]
    lines = [ln if ln[0] != 31 else (31, 32, 6.0, -80.0, 56.0) for ln in lines]
    return [*lines, (31, 32, 6.0, 64.0, 84.0), (31, 32, 3.0, 56.0, 64.0)]


def near_line(f: Path, lon: float, width: float, la0: float, la1: float) -> str:
    return (
        f"select id, geometry geom, bbox from read_parquet('{f}') "
        f"where bbox.xmax >= {lon - width} and bbox.xmin <= {lon + width} "
        f"and bbox.ymax >= {la0} and bbox.ymin <= {la1}"
    )


def overlap(a: str, b: str, share: float) -> str:
    return (
        f"st_intersects({a}.geom, {b}.geom) and st_area(st_intersection({a}.geom, {b}.geom)) "
        f">= {share} * least(st_area({a}.geom), st_area({b}.geom))"
    )


def bbox_meet(a: str, b: str) -> str:
    return (
        f"{a}.bbox.xmin <= {b}.bbox.xmax and {b}.bbox.xmin <= {a}.bbox.xmax "
        f"and {a}.bbox.ymin <= {b}.bbox.ymax and {b}.bbox.ymin <= {a}.bbox.ymax"
    )


def cross_zone(c: duckdb.DuckDBPyConnection, root: Path, year: int, width: float = 0.3) -> dict:
    have = {int(p.parent.name[5:]): p for p in files(root, year)}
    found, pairs = {}, 0
    for zw, ze, lon, la0, la1 in zone_lines():
        if zw not in have or ze not in have:
            continue
        w = near_line(have[zw], lon, width, la0, la1)
        e = near_line(have[ze], lon, width, la0, la1)
        n = one(c, f"with w as ({w}), e as ({e}) select count(*) from w join e "
                   f"on {bbox_meet('w', 'e')} where {overlap('w', 'e', 0.5)}")
        pairs += n
        if n:
            found[f"{zw}|{ze}@{lon}"] = n
    return {"pairs": pairs, "lines": found}


def equator(c: duckdb.DuckDBPyConnection, root: Path, year: int) -> int:
    n = 0
    for f in files(root, year):
        q = (
            f"select id, geometry geom, bbox from read_parquet('{f}') "
            "where bbox.ymin <= 0.05 and bbox.ymax >= -0.05"
        )
        n += one(
            c,
            f"with p as ({q}) select count(*) from p a join p b "
            f"on a.id < b.id and split_part(a.id, '-', 1) <> split_part(b.id, '-', 1) "
            f"and {bbox_meet('a', 'b')} "
            f"where (substr(a.id, 3, 1) >= 'N') <> (substr(b.id, 3, 1) >= 'N') "
            f"and {overlap('a', 'b', 0.1)}",
        )
    return n


def sea(root: Path, year: int, per_zone: int, land: Path) -> dict:
    rng = np.random.default_rng(0)
    n = dropped = 0
    for f in files(root, year):
        pf = pq.ParquetFile(f)
        groups = rng.choice(
            pf.num_row_groups, min(pf.num_row_groups, max(1, per_zone // 8192)), False
        )
        t = pf.read_row_groups(sorted(groups.tolist()), columns=["bbox", "geometry"])
        if t.num_rows > per_zone:
            t = t.take(np.sort(rng.choice(t.num_rows, per_zone, False)))
        flt = SeaFilter(land, int(f.parent.name[5:]))
        for _ in flt(t.to_batches()):
            pass
        n += t.num_rows
        dropped += flt.dropped
    return {"sampled": n, "sea": dropped, "share": round(dropped / max(n, 1), 6)}


def tile_frames(footprints: Path) -> dict[str, tuple[str, float, float]]:
    """tile -> (UTM EPSG, raster top northing, raster west easting) from the footprints."""
    out = {}
    for r in pq.read_table(footprints, columns=["tile_key", "geometry"]).to_pylist():
        tk = r["tile_key"]
        epsg = f"EPSG:{(32600 if tk[2] >= 'N' else 32700) + int(tk[:2])}"
        xy = shapely.get_coordinates(shapely.from_wkb(r["geometry"]))
        tr = Transformer.from_crs("EPSG:4326", epsg, always_xy=True)
        x, y = tr.transform(xy[:, 0], xy[:, 1])
        out[tk] = (epsg, float(np.max(y)), float(np.min(x)))
    return out


def north_edge(root: Path, year: int, frames: dict) -> dict:
    top_n: dict[str, int] = {}
    ref_n: dict[str, int] = {}
    for f in files(root, year):
        t = pq.read_table(f, columns=["id", "bbox"])
        tk = pc.utf8_slice_codeunits(t["id"], 0, 9).to_numpy(zero_copy_only=False)
        bb = t["bbox"].combine_chunks()
        lon = (pc.struct_field(bb, "xmin").to_numpy() + pc.struct_field(bb, "xmax").to_numpy()) / 2
        lat = (pc.struct_field(bb, "ymin").to_numpy() + pc.struct_field(bb, "ymax").to_numpy()) / 2
        order = np.argsort(tk, kind="stable")
        keys, starts = np.unique(tk[order], return_index=True)
        for k, s, e in zip(keys, starts, [*starts[1:], len(order)], strict=True):
            if k not in frames:
                continue
            epsg, top, west = frames[k]
            i = order[s:e]
            x, y = Transformer.from_crs("EPSG:4326", epsg, always_xy=True).transform(lon[i], lat[i])
            d = top - y
            west_part = (x - west) < 61_000
            top_n[k] = top_n.get(k, 0) + int(((d >= 0) & (d < 1000) & west_part).sum())
            ref_n[k] = ref_n.get(k, 0) + int(((d >= 1500) & (d < 4500) & west_part).sum())
    per_km = {k: ref_n[k] / 3 for k in ref_n}  # the reference band is 3 km deep
    pile = [k for k in top_n if top_n[k] >= 30 and top_n[k] > 3 * per_km[k]]
    gap = [k for k in top_n if per_km[k] >= 30 and top_n[k] < per_km[k] / 3]
    return {"tiles": len(top_n), "pileup": len(pile), "gap": len(gap),
            "pileup_eg": sorted(pile)[:10], "gap_eg": sorted(gap)[:10]}


def run(root: Path, year: int, a: argparse.Namespace, full: bool) -> dict[str, Any]:
    c = duckdb.connect()
    c.sql(f"load spatial; set threads={a.threads}; set memory_limit='{a.memory_limit}'")
    out: dict[str, Any] = {"root": str(root), "totals": totals(c, root, year, a.max_km2 * 1e6),
                           "strips": strips(c, root, year)}
    if full:
        out["dup_ids"] = dup_ids(c, root, year)
        out["cross_zone"] = cross_zone(c, root, year)
        out["equator_pairs"] = equator(c, root, year)
        if a.land_polygons:
            out["sea"] = sea(root, year, a.sea_per_zone, a.land_polygons)
        if a.frames:
            out["north_edge"] = north_edge(root, year, a.frames)
    print(f"  {root}: done", flush=True)
    return out


def compare(new: dict, old: dict, tol: float) -> dict:
    return {
        "n": round(new["n"] / old["n"] - 1, 4),
        "km2": round(new["km2"] / old["km2"] - 1, 4),
        "zones_moved": {
            z: round(v["n"] / old["zones"][z]["n"] - 1, 3)
            for z, v in new["zones"].items()
            if z in old["zones"] and abs(v["n"] / old["zones"][z]["n"] - 1) > tol
        },
        "zones_only_new": sorted(set(new["zones"]) - set(old["zones"])),
        "zones_only_old": sorted(set(old["zones"]) - set(new["zones"])),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("year", type=int)
    ap.add_argument("--root", type=Path, required=True, help="holds {year}/zone=NN/utmNN.parquet")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--release", type=Path, help="a released tree to compare with")
    ap.add_argument("--release-full", action="store_true",
                    help="run every check on the release too, not only totals and strips")
    ap.add_argument("--prev-year", type=int)
    ap.add_argument("--prev-root", type=Path)
    ap.add_argument("--max-km2", type=float, default=5.0)
    ap.add_argument("--zone-tol", type=float, default=0.15)
    ap.add_argument("--land-polygons", type=Path, default=os.environ.get("FTW_LAND_POLYGONS"))
    ap.add_argument("--sea-per-zone", type=int, default=20000)
    ap.add_argument("--footprints", type=Path, help="tile footprints for the north-edge check")
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 8)))
    ap.add_argument("--memory-limit", default="16GB")
    a = ap.parse_args()
    a.frames = tile_frames(a.footprints) if a.footprints else None
    rep: dict[str, Any] = {"year": a.year, "new": run(a.root, a.year, a, True)}
    if a.release and (a.release / str(a.year)).is_dir():
        rep["release"] = run(a.release, a.year, a, a.release_full)
    if a.prev_year and a.prev_root:
        rep["prev"] = {"year": a.prev_year, **run(a.prev_root, a.prev_year, a, False)}
    for ref in ("release", "prev"):
        if ref in rep:
            rep[f"vs_{ref}"] = compare(rep["new"]["totals"], rep[ref]["totals"], a.zone_tol)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    s = rep["new"]
    t = s["totals"]
    print(f"{a.year}: {t['n']:,} parcels, {t['km2']:,.0f} km2, {t['zone_files']} zones; "
          f"max {t['max_area_m2'] / 1e6:.3f} km2, over cap {t['over_cap']}; "
          f"dup ids {s['dup_ids']}; cross-zone pairs {s['cross_zone']['pairs']}; "
          f"equator pairs {s['equator_pairs']}")
    if "sea" in s:
        print(f"sea {s['sea']['sea']}/{s['sea']['sampled']}")
    if "north_edge" in s:
        ne = s["north_edge"]
        print(f"north edge pile-up {ne['pileup']} gap {ne['gap']} of {ne['tiles']}")
    print("strips", s["strips"])
    for ref in ("release", "prev"):
        if f"vs_{ref}" in rep:
            print(f"vs {ref}:", rep[f"vs_{ref}"])


if __name__ == "__main__":
    main()
