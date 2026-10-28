"""Union claimed orphan-strip parcels with the overlapping pieces kept by the next zone.

A tile claims parcels of its square that lie in an adjacent zone's unowned strip (``ownership.py``,
column ``claimed``). A claimed field that reaches into that zone's first square is also seen, cut
at the raster edge, by the tile there, which keeps its piece. ``fiboa_convert.join_seams`` unions
such pieces inside a zone file; this pass does it across zone files. Each claimed parcel is paired
with the parcels of the two neighbouring zone files by the same rule (overlap >= 10% of the
smaller and >= 100 m2). Connected pieces are unioned into the claimed parcel (the smallest claimed
id of the group, pf_mean area-weighted, area_m2 recomputed from the union) and the other pieces
are removed from their zone files.

``merge_polygons.py`` runs it on ``{root}/{year}/zone=NN/part-0.parquet`` after writing the zones.
The rewritten files keep merge's order, layout and footer fingerprint, and gain a
``cross_zone_seams`` footer key. Writes ``_cross_zone.json``.

    python cross_zone_seams.py --root merged --year 2025
"""

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

from fiboa_convert import JOIN_MIN_OVERLAP_M2, WINDOW_MIN_OVERLAP_FRAC, components

WORLD = "{'min_x': -180.0, 'min_y': -90.0, 'max_x': 180.0, 'max_y': 90.0}::BOX_2D"
COLS = "tile_key, parcel_id, area_m2, pf_mean, xmin, ymin, xmax, ymax, ST_AsWKB(geometry) AS wkb"
SEAM_KEY = b"cross_zone_seams"


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.sql("load spatial")
    return con


def polygon_union(g: shapely.Geometry) -> shapely.Geometry:
    """Union of the polygons in ``g`` at any nesting depth (make_valid can nest collections)."""
    parts = shapely.get_parts(g)
    while any(p.geom_type in ("MultiPolygon", "GeometryCollection") for p in parts):
        parts = shapely.get_parts(parts)
    return shapely.union_all([p for p in parts if p.geom_type == "Polygon"])


def zone_files(root: Path, year: int) -> dict[int, Path]:
    return {
        int(p.parent.name.split("=")[1]): p
        for p in sorted((root / str(year)).glob("zone=*/part-0.parquet"))
    }


def _has_claims(f: Path) -> bool:
    return "claimed" in pq.ParquetFile(f).schema_arrow.names


def _rows(con: duckdb.DuckDBPyConnection, f: Path, where: str) -> dict:
    t = con.sql(f"SELECT {COLS} FROM read_parquet('{f}') WHERE {where}").arrow().read_all()
    d = {c: t[c].to_numpy(zero_copy_only=False) for c in t.column_names if c != "wkb"}
    d["geom"] = shapely.from_wkb(t["wkb"].to_numpy(zero_copy_only=False))
    d["id"] = np.array([f"{a}-{b}" for a, b in zip(d["tile_key"], d["parcel_id"], strict=True)])
    return d


def _concat(parts: list[dict]) -> dict:
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def _valid(geoms: np.ndarray) -> np.ndarray:
    """Polygonal part of make_valid for the invalid rows, the others untouched."""
    out = np.array(geoms, dtype=object, copy=True)
    bad = ~shapely.is_valid(out)
    if bad.any():
        out[bad] = [polygon_union(g) for g in shapely.make_valid(out[bad])]
    return out


def plan(root: Path, year: int, sources: set[int] | None = None) -> dict:
    """Unions (claimed id -> merged row) and ids to drop, per zone.

    ``sources``: the zones whose claimed parcels are paired (default all).
    """
    con = _con()
    files = zone_files(root, year)
    unions: dict[int, list[dict]] = {}
    drops: dict[int, set[str]] = {}
    pairs_out = []
    # zones run in order, and a piece already absorbed into (or kept as) a union of an earlier
    # zone is left alone, so two claimed pieces that claim each other merge once, into the lower
    taken: set[str] = set()
    for z, f in files.items():
        if (sources is not None and z not in sources) or not _has_claims(f):
            continue
        c = _rows(con, f, "coalesce(claimed, false)")
        if not len(c["id"]):
            continue
        w, s, e, n = c["xmin"].min(), c["ymin"].min(), c["xmax"].max(), c["ymax"].max()
        box = f"xmin <= {e} AND xmax >= {w} AND ymin <= {n} AND ymax >= {s}"
        nb = [_rows(con, files[zz], box) for zz in ((z - 2) % 60 + 1, z % 60 + 1) if zz in files]
        nb = [d for d in nb if len(d["id"])]
        if not nb:
            continue
        o = _concat(nb)
        i, j = shapely.STRtree(o["geom"]).query(c["geom"], predicate="intersects")
        if not i.size:
            continue
        tr = Transformer.from_crs(4326, 32600 + z, always_xy=True)
        # merged rows are unrepaired coverage-simplify output, a few of them OGC-invalid, and
        # GEOS overlay on an invalid ring raises TopologyException
        x = shapely.intersection(_valid(c["geom"][i]), _valid(o["geom"][j]))
        ov = shapely.area(
            shapely.transform(x, lambda xy, tr=tr: np.column_stack(tr.transform(*xy.T)))
        )
        amin = np.minimum(c["area_m2"][i], o["area_m2"][j])
        ok = (ov >= WINDOW_MIN_OVERLAP_FRAC * amin) & (ov >= JOIN_MIN_OVERLAP_M2)
        ok &= ~np.isin(c["id"][i], list(taken)) & ~np.isin(o["id"][j], list(taken))
        comp = components([(c["id"][p], o["id"][q]) for p, q in zip(i[ok], j[ok], strict=True)])
        groups: dict[str, list[str]] = {}
        for m, g in comp.items():
            groups.setdefault(g, []).append(m)
        allrows = _concat([c, o])
        at = {k: v for v, k in enumerate(allrows["id"])}
        cset = set(c["id"])
        for members in groups.values():
            keep = min(m for m in members if m in cset)
            idx = [at[m] for m in members]
            g = polygon_union(shapely.make_valid(shapely.union_all(_valid(allrows["geom"][idx]))))
            a = allrows["area_m2"][idx].astype(float)
            pf = float((a * allrows["pf_mean"][idx]).sum() / a.sum())
            xmin, ymin, xmax, ymax = g.bounds
            gu = shapely.transform(g, lambda xy, tr=tr: np.column_stack(tr.transform(*xy.T)))
            tk, pid = keep.rsplit("-", 1)
            unions.setdefault(z, []).append({
                "tile_key": tk, "parcel_id": int(pid), "wkb": shapely.to_wkb(g), "pf_mean": pf,
                "area_m2": float(shapely.area(gu)),
                "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
            })
            taken.update(members)
            for m in members:
                if m != keep:
                    drops.setdefault(int(m[:2]), set()).add(m)
        pairs_out += [
            {"zone": z, "claimed": c["id"][p], "other": o["id"][q], "overlap_m2": float(v)}
            for p, q, v in zip(i[ok], j[ok], ov[ok], strict=True)
        ]
    return {"unions": unions, "drops": drops, "pairs": pairs_out}


def rewrite(f: Path, unions: list[dict], drops: set[str]) -> Path:
    """Zone file without ``drops`` and with the union rows' geometry and stats, in merge's order,
    written next to ``f``; ``run`` swaps them in once every zone is written."""
    con = _con()
    con.register("drops", pa.table({"id": pa.array(sorted(drops), pa.string())}))
    u = unions or [{"tile_key": "", "parcel_id": -1, "wkb": b"", "pf_mean": 0.0, "area_m2": 0.0,
                    "xmin": 0.0, "ymin": 0.0, "xmax": 0.0, "ymax": 0.0}]
    con.register("u", pa.Table.from_pylist(u))
    # merge's footer keys (its fingerprint) carry over; DuckDB writes ``geo`` itself
    old = pq.ParquetFile(f).schema_arrow.metadata or {}
    md = {k.decode(): v.decode() for k, v in old.items() if k not in (b"ARROW:schema", b"geo")}
    md[SEAM_KEY.decode()] = "1"
    kv = ", ".join(f"{k}: '{v.replace(chr(39), chr(39) * 2)}'" for k, v in md.items())
    tmp = f.with_name("part-0.parquet.cz-tmp")  # not *.parquet: zone=*/*.parquet globs skip it
    con.sql(
        f"""COPY (
          SELECT p.* REPLACE (
            CASE WHEN u.wkb IS NULL THEN p.geometry ELSE ST_GeomFromWKB(u.wkb) END AS geometry,
            coalesce(u.area_m2, p.area_m2) AS area_m2, coalesce(u.pf_mean, p.pf_mean) AS pf_mean,
            coalesce(u.xmin, p.xmin) AS xmin, coalesce(u.ymin, p.ymin) AS ymin,
            coalesce(u.xmax, p.xmax) AS xmax, coalesce(u.ymax, p.ymax) AS ymax)
          FROM read_parquet('{f}') p
          LEFT JOIN u ON u.tile_key = p.tile_key AND u.parcel_id = p.parcel_id
          WHERE p.tile_key || '-' || p.parcel_id NOT IN (SELECT id FROM drops)
          ORDER BY ST_Hilbert(ST_Centroid(geometry), {WORLD})
        ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 50000,
                      KV_METADATA {{{kv}}})"""
    )
    return tmp


def run(root: Path, year: int, sources: set[int] | None = None) -> dict:
    p = plan(root, year, sources)
    files = zone_files(root, year)
    report = {"year": year, "zones": {}}
    zones = sorted(set(p["unions"]) | set(p["drops"]))
    # write every file before swapping any, so a failure part-way leaves each zone file as
    # merge wrote it and a rerun plans from the same input
    tmps = {z: rewrite(files[z], p["unions"].get(z, []), p["drops"].get(z, set())) for z in zones}
    for z in zones:
        report["zones"][f"{z:02d}"] = {
            "unions": len(p["unions"].get(z, [])),
            "dropped": len(p["drops"].get(z, set())),
            "rows": pq.ParquetFile(tmps[z]).metadata.num_rows,
        }
    for z in zones:
        tmps[z].replace(files[z])
    report["pairs"] = p["pairs"]
    (root / str(year) / "_cross_zone.json").write_text(json.dumps(report, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    a = ap.parse_args()
    r = run(a.root, a.year)
    print(json.dumps(r["zones"]), f"{len(r['pairs'])} pairs", flush=True)


if __name__ == "__main__":
    main()
