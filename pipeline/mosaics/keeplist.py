"""Cropland tile list for ``download.py --tile-list``. Tiles that fail to score are kept.

    python3 keeplist.py bbox --out tile_footprints.parquet
    python3 keeplist.py score --footprints tile_footprints.parquet \\
        --out global_cropland.parquet --keep-list global_keep.txt
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import shape
from stac import MosaicItem, search_items

from cropland import cropland_fraction

# Several quarters because seasonal coverage differs (polar winter).
SWEEP = [(y, q) for y in (2024, 2020, 2017) for q in ("Q3", "Q2", "Q4", "Q1")]


def footprint_row(item: MosaicItem) -> dict:
    geom = shape(item.geometry)
    # Score an antimeridian tile on its largest part; the combined bounds span the globe.
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    sw, ss, se, sn = max(parts, key=lambda g: g.area).bounds
    w, s, e, n = item.bbox
    return {
        "tile_key": item.tile_key,
        "geometry": shapely.to_wkb(geom),
        "west": w,
        "south": s,
        "east": e,
        "north": n,
        "score_west": sw,
        "score_south": ss,
        "score_east": se,
        "score_north": sn,
        "antimeridian": e - w > 180,
    }


def score_one(tile_key: str, bbox: tuple[float, float, float, float]) -> dict:
    try:
        stat = cropland_fraction(tile_key, bbox)
    except Exception as exc:  # noqa: BLE001
        return {"tile_key": tile_key, "fraction": 1.0, "valid_px": 0, "error": type(exc).__name__}
    return {"tile_key": tile_key, "fraction": stat.fraction, "valid_px": stat.valid_px, "error": ""}


def cmd_bbox(args: argparse.Namespace) -> None:
    found: dict[str, MosaicItem] = {}
    for year, quarter in SWEEP:
        n = len(found)
        for item in search_items((-180, -90, 180, 90), year, (quarter,), bands=(), page_limit=1000):
            found.setdefault(item.tile_key, item)
        print(f"{year} {quarter}: {len(found):,} tiles (+{len(found) - n:,})", flush=True)
    rows = [footprint_row(found[k]) for k in sorted(found)]
    pq.write_table(pa.Table.from_pylist(rows), args.out)


def cmd_score(args: argparse.Namespace) -> None:
    footprints = pq.read_table(args.footprints).to_pylist()
    rows = []
    with ThreadPoolExecutor(args.workers) as pool:
        futs = [
            pool.submit(
                score_one,
                r["tile_key"],
                (r["score_west"], r["score_south"], r["score_east"], r["score_north"]),
            )
            for r in footprints
        ]
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            if i % 2000 == 0:
                print(f"{i:,}/{len(futs):,}", flush=True)
    pq.write_table(pa.Table.from_pylist(rows), args.out)
    keep = sorted(r["tile_key"] for r in rows if r["fraction"] > args.threshold)
    args.keep_list.write_text("\n".join(keep) + "\n")
    errors = sum(bool(r["error"]) for r in rows)
    print(f"kept {len(keep):,}/{len(rows):,}, {errors:,} of them unscored")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(required=True)
    b = sub.add_parser("bbox", help="tile footprints from STAC")
    b.add_argument("--out", type=Path, default=Path("tile_footprints.parquet"))
    b.set_defaults(func=cmd_bbox)
    s = sub.add_parser("score", help="io-lulc crop fraction per footprint")
    s.add_argument("--footprints", type=Path, default=Path("tile_footprints.parquet"))
    s.add_argument("--out", type=Path, default=Path("global_cropland.parquet"))
    s.add_argument("--keep-list", type=Path, default=Path("global_keep.txt"))
    s.add_argument("--threshold", type=float, default=0.01)
    s.add_argument("--workers", type=int, default=16)
    s.set_defaults(func=cmd_score)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
