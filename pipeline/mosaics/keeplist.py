"""Global cropland keep-list: which MGRS tiles ``download.py`` pulls at all.

Most of Earth's ~35,000 Sentinel-2 MGRS sub-tiles are ocean, desert, ice or forest, so
stage 1 only downloads tiles with cropland per Esri io-lulc (max crop fraction over
2017/2020/2024 -- see cropland.py). The MGRS grid is year-independent, so this is built
once and reused for every imagery year via ``download.py --tile-list``.

  bbox   sweep the public STAC collection across a fixed set of quarters (seasonal
         coverage differs, e.g. polar winter) to collect tile_key -> geometry/bbox once
  score  io-lulc crop fraction per footprint via /vsicurl, thread-pooled; writes the
         scored table and the keep-list download.py reads

A tile io-lulc could not be scored keeps it: a false keep costs one wasted tile of
download and inference, a false skip silently drops a real cropland tile forever.

Usage:
    python3 pipeline/mosaics/keeplist.py bbox  --out tile_footprints.parquet
    python3 pipeline/mosaics/keeplist.py score --footprints tile_footprints.parquet \\
        --out global_cropland.parquet --keep-list global_keep.txt
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import shape
from stac import MosaicItem, search_items

from cropland import IOLULC_YEARS, cropland_fraction_iolulc

GLOBAL_BBOX = (-180.0, -90.0, 180.0, 90.0)
# Seasonal coverage differs (polar winter etc.), so several quarters are swept rather
# than assuming one suffices. There is no independent manifest to reconcile against
# here, so watch the per-quarter "+N new" counts printed below: they should fall to ~0
# by the end of this list, and the list should grow if they have not.
SWEEP_ORDER = [(y, q) for y in (2024, 2020, 2017) for q in ("Q3", "Q2", "Q4", "Q1")]


def footprint_row(tile_key: str, item: MosaicItem) -> dict:
    """One ``tile_bboxes`` row: the tile's footprint, plus a sane window to sample land
    cover in even when the tile itself crosses the antimeridian."""
    geom = shape(item.geometry)
    # Scoring bbox = bounds of the LARGEST part. A dateline tile's parts sit at +180
    # and -180; their combined bounds span the globe, but each part alone is a sane
    # window to sample land cover in.
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    sw, ss, se, sn = max(parts, key=lambda g: g.area).bounds
    w, s, e, n = item.bbox
    return {
        "tile_key": tile_key,
        "geometry": shapely.to_wkb(geom),
        "west": w,
        "south": s,
        "east": e,
        "north": n,
        "score_west": sw,
        "score_south": ss,
        "score_east": se,
        "score_north": sn,
        "antimeridian": bool(e - w > 180),
    }


def cmd_bbox(args: argparse.Namespace) -> None:
    found: dict[str, MosaicItem] = {}
    for year, quarter in SWEEP_ORDER:
        t0 = time.time()
        before = len(found)
        # bands=() skips the per-band asset check: only geometry/bbox is wanted here.
        for item in search_items(GLOBAL_BBOX, year, (quarter,), bands=(), page_limit=1000):
            found.setdefault(item.tile_key, item)
        print(
            f"  {year} {quarter}: {len(found):,} tiles total "
            f"(+{len(found) - before:,} new, {time.time() - t0:.0f}s)"
        )

    rows = [footprint_row(tk, item) for tk, item in sorted(found.items())]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), args.out)
    n_am = sum(r["antimeridian"] for r in rows)
    print(f"wrote {args.out}: {len(rows):,} footprints ({n_am:,} antimeridian-crossing)")


def _score_one(tile_key: str, bbox: tuple[float, float, float, float]) -> dict:
    try:
        st = cropland_fraction_iolulc(tile_key, bbox)
    except Exception as exc:
        # fraction=1.0 so an unscored tile is KEPT -- see the module docstring.
        return {"tile_key": tile_key, "fraction": 1.0, "valid_px": 0, "error": type(exc).__name__}
    return {"tile_key": tile_key, "fraction": st.fraction, "valid_px": st.valid_px, "error": ""}


def keep_tiles(rows: list[dict], threshold: float) -> list[str]:
    "Tile keys scoring above ``threshold``, sorted for a stable keep-list file."
    return sorted(r["tile_key"] for r in rows if r["fraction"] > threshold)


def cmd_score(args: argparse.Namespace) -> None:
    footprints = pq.read_table(args.footprints).to_pylist()
    print(
        f"scoring {len(footprints):,} footprints against io-lulc {IOLULC_YEARS}, "
        f"{args.workers} workers"
    )

    rows: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(
                _score_one,
                r["tile_key"],
                (r["score_west"], r["score_south"], r["score_east"], r["score_north"]),
            ): r
            for r in footprints
        }
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            if i % 2000 == 0:
                rate = i / (time.time() - t0)
                print(f"  {i:,}/{len(footprints):,}  {rate:.0f}/s  eta {(len(footprints) - i) / rate / 60:.0f} min")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), args.out)

    errs = [r for r in rows if r["error"]]
    keep = keep_tiles(rows, args.threshold)
    args.keep_list.write_text("\n".join(keep) + "\n")

    pct = 100 * len(keep) / len(rows)
    print(f"\nwrote {args.out} and {args.keep_list}")
    print(f"  keep {len(keep):,}/{len(rows):,} ({pct:.1f}%) at cropland > {args.threshold}")
    print(f"  skip {len(rows) - len(keep):,} ({100 - pct:.1f}%) -- ocean/desert/ice/forest")
    print(f"  errored (kept anyway): {len(errs):,}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bbox", help="collect tile_key -> geometry/bbox from STAC, once")
    b.add_argument("--out", type=Path, default=Path("tile_footprints.parquet"))
    b.set_defaults(func=cmd_bbox)

    s = sub.add_parser("score", help="io-lulc crop fraction per footprint (max over epochs)")
    s.add_argument("--footprints", type=Path, default=Path("tile_footprints.parquet"))
    s.add_argument("--out", type=Path, default=Path("global_cropland.parquet"))
    s.add_argument("--keep-list", type=Path, default=Path("global_keep.txt"))
    s.add_argument(
        "--threshold", type=float, default=0.01, help="minimum cropland fraction to keep a tile"
    )
    s.add_argument("--workers", type=int, default=16)
    s.set_defaults(func=cmd_score)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
