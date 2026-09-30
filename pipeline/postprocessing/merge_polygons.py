"Filter owned parcels and merge by UTM zone."

import argparse
import json
import os
import time
from pathlib import Path

import duckdb

IN_ROOT = Path("simplified")
AUX_ROOT = Path("aux")
OUT_ROOT = Path("merged")
WORLD = "{'min_x': -180.0, 'min_y': -90.0, 'max_x': 180.0, 'max_y': 90.0}::BOX_2D"


def read_tiles(path: Path) -> set[str]:
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}


def find_gaps(
    have: set[str], expected: set[str], empty: set[str], aux_have: set[str] | None
) -> dict[str, list[str]]:
    gaps = {"tiles": sorted(expected - empty - have)}
    if aux_have is not None:
        gaps["aux"] = sorted(have - aux_have)
    return {k: v for k, v in gaps.items() if v}


def report_gaps(gaps: dict[str, list[str]], allow_missing: bool) -> None:
    if not gaps:
        return
    for kind, tiles in gaps.items():
        head = ", ".join(tiles[:20]) + (" ..." if len(tiles) > 20 else "")
        print(f"missing {kind}: {len(tiles)} ({head})", flush=True)
    if not allow_missing:
        raise SystemExit("incomplete inputs; fix them or pass --allow-missing")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--in-root", type=Path, default=IN_ROOT)
    ap.add_argument("--aux-root", type=Path, default=AUX_ROOT)
    ap.add_argument("--no-aux", action="store_true", help="skip the patch-statistics join")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT)
    ap.add_argument("--keep-list", type=Path, required=True)
    ap.add_argument("--empty-list", type=Path, required=True)
    ap.add_argument("--allow-missing", action="store_true", help="merge despite missing tiles")
    ap.add_argument("--max-km2", type=float, default=5.0)
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 8)))
    ap.add_argument("--memory", default="48GB")
    a = ap.parse_args()

    src, out = a.in_root / str(a.year), a.out_root / str(a.year)
    aux = None if a.no_aux else a.aux_root / str(a.year)
    expected, empty = read_tiles(a.keep_list), read_tiles(a.empty_list)
    have = {p.stem for p in src.glob("*.parquet")}
    unexpected = have - expected
    if unexpected:
        raise SystemExit(f"unexpected input tiles: {sorted(unexpected)[:20]}")
    aux_have = {p.stem for p in aux.glob("*.parquet")} if aux else None
    report_gaps(
        find_gaps(have, expected, empty, aux_have),
        a.allow_missing,
    )
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql("install spatial; load spatial")
    con.sql(f"set threads={a.threads}; set memory_limit='{a.memory}'")
    con.sql(f"set temp_directory='{out / '_tmp'}'")
    keep = f"in_utm_zone AND in_mgrs_square AND area_m2 <= {a.max_km2 * 1e6}"

    zones = sorted({p.stem[:2] for p in src.glob("*.parquet")})
    summary: dict = {
        "year": a.year,
        "max_km2": a.max_km2,
        "filter": keep,
        "source": str(src),
        "aux": str(aux),
        "zones": {},
    }
    t_all = time.perf_counter()
    for z in zones:
        t = time.perf_counter()
        files = f"'{src}/{z}*.parquet'"
        select = f"SELECT p.* FROM read_parquet({files}) p"
        if aux:
            select = (
                f"SELECT p.*, x.patch_sat_mean, x.patch_sat_max, x.patch_pb_mean "
                f"FROM read_parquet({files}) p LEFT JOIN read_parquet('{aux}/{z}*.parquet') x "
                "USING (tile_key, parcel_id)"
            )
        stats = con.sql(
            f"select count(*), sum(({keep})::int), sum(area_m2)/1e6, "
            f"sum(case when {keep} then area_m2 end)/1e6 from read_parquet({files})"
        ).fetchone()
        assert stats is not None
        n_in, n_out, km2_in, km2_out = (v or 0 for v in stats)
        if not n_out:
            print(f"zone {z}: no retained parcels", flush=True)
            continue
        part = out / f"zone={z}"
        part.mkdir(exist_ok=True)
        tmp = part / "part-0.tmp.parquet"
        con.sql(
            f"COPY ({select} WHERE {keep} "
            f"ORDER BY ST_Hilbert(ST_Centroid(geometry), {WORLD})) "
            f"TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 50000)"
        )
        tmp.replace(part / "part-0.parquet")
        dt = time.perf_counter() - t
        summary["zones"][z] = {
            "parcels_in": n_in,
            "parcels_out": n_out,
            "km2_in": km2_in,
            "km2_out": km2_out,
        }
        print(
            f"zone {z}: {n_in:,} -> {n_out:,} parcels, {km2_in:,.0f} -> {km2_out:,.0f} km2, "
            f"{(part / 'part-0.parquet').stat().st_size / 1e9:.2f} GB, {dt:.0f}s",
            flush=True,
        )
    if not summary["zones"]:
        raise SystemExit("no input polygon zones")
    tot = {
        k: sum(v[k] for v in summary["zones"].values())
        for k in next(iter(summary["zones"].values()))
    }
    summary["total"] = tot
    summary["seconds"] = time.perf_counter() - t_all
    (out / "_summary.json").write_text(json.dumps(summary, indent=1))
    print(
        f"total: {tot['parcels_in']:,} -> {tot['parcels_out']:,} parcels, "
        f"{tot['km2_in']:,.0f} -> {tot['km2_out']:,.0f} km2, {summary['seconds'] / 60:.0f} min"
    )


if __name__ == "__main__":
    main()
