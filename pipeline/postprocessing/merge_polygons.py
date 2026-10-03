"Filter owned parcels and merge by UTM zone."

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

IN_ROOT = Path("simplified")
AUX_ROOT = Path("aux")
OUT_ROOT = Path("merged")
TMP_ROOT = Path("scratch/duckdb")
WORLD = "{'min_x': -180.0, 'min_y': -90.0, 'max_x': 180.0, 'max_y': 90.0}::BOX_2D"

#: Columns the optional patch-QA table contributes. Emitted as NULL when a zone has
#: no aux file, so every zone file in a year has the same schema either way.
AUX_COLUMNS = ("patch_sat_mean", "patch_sat_max", "patch_pb_mean")

FP_KEY = b"merge_fingerprint"
#: Set by outlines.py; carried through simplify's schema-metadata copy.
OUTLINE_PROVENANCE = b"outline_provenance"


def read_tiles(path: Path) -> set[str]:
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}


def classify(have: set[str], expected: set[str], empty: set[str]) -> tuple[set[str], set[str]]:
    """``(unexpected inputs, expected but absent)``.

    Both guards subtract ``empty`` -- they used to disagree. ``find_gaps`` excused a
    tile listed in empty.txt while the ``unexpected`` check did not, so the
    invocation the README documents aborted: tiles.txt and empty.txt are disjoint
    lists, and outlines writes a parquet for every zero-parcel tile which simplify
    carries forward, so a verified-empty tile always arrives as an input that is not
    in the keep list. Measured before this change: "unexpected input tiles:
    ['31UFT']", exit 1, on exactly the documented command.
    """
    return have - expected - empty, expected - empty - have


def find_gaps(
    have: set[str], expected: set[str], empty: set[str], aux_have: set[str] | None
) -> dict[str, list[str]]:
    _, absent = classify(have, expected, empty)
    gaps = {"tiles": sorted(absent)}
    if aux_have is not None:
        gaps["aux"] = sorted(have - empty - aux_have)
    return {k: v for k, v in gaps.items() if v}


def report_gaps(gaps: dict[str, list[str]], allow_missing: bool) -> None:
    if not gaps:
        return
    for kind, tiles in gaps.items():
        head = ", ".join(tiles[:20]) + (" ..." if len(tiles) > 20 else "")
        print(f"missing {kind}: {len(tiles)} ({head})", flush=True)
    if not allow_missing:
        raise SystemExit("incomplete inputs; fix them or pass --allow-missing")


def aux_files(aux: Path | None, tiles: list[str]) -> list[Path]:
    """The aux file for each named tile, matched EXACTLY.

    A ``{aux}/{zone}*.parquet`` glob over-matches: with a stray
    ``31UFS_0_0_rerun.parquet`` beside ``31UFS_0_0.parquet`` both matched ``31*``
    and the LEFT JOIN fanned out. Measured, 3 input parcels became 6 written rows
    over 3 distinct keys while the run printed "3 -> 3 parcels" -- the stats query
    bypasses the join, so every reported count was the pre-join count -- and fiboa
    published duplicate parcel ids, which catalog/vector/{year}/AGENTS.md promises
    cannot happen.
    """
    if aux is None:
        return []
    return [p for p in (aux / f"{tk}.parquet" for tk in tiles) if p.is_file()]


def check_aux_unique(con: duckdb.DuckDBPyConnection, files: list[Path]) -> None:
    "Refuse to join an aux table that is not unique on (tile_key, parcel_id)."
    lst = ", ".join(f"'{p}'" for p in files)
    row = con.sql(
        f"SELECT count(*), count(DISTINCT (tile_key, parcel_id)) FROM read_parquet([{lst}])"
    ).fetchone()
    assert row is not None
    if row[0] != row[1]:
        raise SystemExit(
            f"aux tables are not unique on (tile_key, parcel_id): {row[0]:,} rows over "
            f"{row[1]:,} keys. Joining them would duplicate parcels. Files: "
            + ", ".join(p.name for p in files[:10])
        )


def select_sql(files: list[Path], aux: list[Path]) -> str:
    "p.* plus the aux columns, NULL when the zone has no aux file."
    src = ", ".join(f"'{p}'" for p in files)
    if not aux:
        nulls = ", ".join(f"NULL::DOUBLE AS {c}" for c in AUX_COLUMNS)
        return f"SELECT p.*, {nulls} FROM read_parquet([{src}]) p"
    cols = ", ".join(f"x.{c}" for c in AUX_COLUMNS)
    lst = ", ".join(f"'{p}'" for p in aux)
    return (
        f"SELECT p.*, {cols} FROM read_parquet([{src}]) p "
        f"LEFT JOIN read_parquet([{lst}]) x USING (tile_key, parcel_id)"
    )


def fingerprint(files: list[Path], aux: list[Path], keep: str) -> str:
    """Identity of everything that decides a zone's contents.

    Hashed, not spelled out: a zone can hold a couple of hundred tiles and this
    goes into the parquet footer.
    """
    parts = [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(files + aux)]
    body = json.dumps([keep, parts], sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


def is_current(dst: Path, fp: str) -> bool:
    """True when ``dst`` was written from exactly these inputs under this filter.

    Existence alone is not enough -- that is what let regenerated inputs and
    changed flags publish stale zone files -- and a truncated or unstamped file
    must not count as current either.
    """
    if not dst.is_file():
        return False
    try:
        md = pq.ParquetFile(dst).schema_arrow.metadata or {}
    except (OSError, pa.ArrowException):
        return False
    return md.get(FP_KEY, b"").decode() == fp


def _metadata(path: Path) -> dict:
    try:
        return pq.ParquetFile(path).schema_arrow.metadata or {}
    except (OSError, pa.ArrowException):
        return {}


def simplify_tolerance(files: list[Path]) -> float | None:
    "The tolerance simplify_polygons stamped, so the release can state the real one."
    for p in files:
        stamp = _metadata(p).get(b"simplify_fingerprint", b"").decode()
        for field in stamp.split(";"):
            if field.startswith("tol="):
                return float(field[4:])
    return None


def provenance(files: list[Path]) -> dict:
    "The outline stage's stamp, so a release can name the model it came from."
    for p in files:
        raw = _metadata(p).get(OUTLINE_PROVENANCE)
        if raw:
            return json.loads(raw)
    return {}


def outline_specs(files: list[Path]) -> set[str]:
    "Every BoundaryVote method id the outline stage stamped on these tiles."
    out = set()
    for p in files:
        raw = _metadata(p).get(OUTLINE_PROVENANCE)
        if raw and json.loads(raw).get("spec"):
            out.add(json.loads(raw)["spec"])
    return out


def clear_partition(part: Path) -> None:
    """Remove a zone partition whose rerun retained nothing.

    Leaving it meant the previous run's part-0.parquet stayed live with no
    _summary.json entry, and fiboa_convert.discover_zones still converted it: a
    mixed-generation release. Measured, a rerun that retained zero parcels printed
    "no retained parcels" and left the stale file in place.
    """
    if part.exists():
        shutil.rmtree(part)
        print(f"  cleared stale partition {part.name}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--in-root", type=Path, default=IN_ROOT)
    ap.add_argument("--aux-root", type=Path, default=AUX_ROOT)
    ap.add_argument("--no-aux", action="store_true", help="skip the patch-statistics join")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT)
    ap.add_argument(
        "--tmp-dir",
        type=Path,
        default=TMP_ROOT,
        help="DuckDB spill directory; must be outside --out-root",
    )
    ap.add_argument("--keep-list", type=Path, required=True)
    ap.add_argument("--empty-list", type=Path, required=True)
    ap.add_argument("--allow-missing", action="store_true", help="merge despite missing tiles")
    ap.add_argument("--force", action="store_true", help="rewrite zones that are already current")
    ap.add_argument("--max-km2", type=float, default=5.0)
    ap.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 8)))
    ap.add_argument("--memory", default="48GB")
    a = ap.parse_args()

    src, out = a.in_root / str(a.year), a.out_root / str(a.year)
    aux = None if a.no_aux else a.aux_root / str(a.year)
    expected, empty = read_tiles(a.keep_list), read_tiles(a.empty_list)
    have = {p.stem for p in src.glob("*.parquet")}
    unexpected, _ = classify(have, expected, empty)
    if unexpected:
        raise SystemExit(
            f"input tiles in neither --keep-list nor --empty-list: {sorted(unexpected)[:20]}"
        )
    aux_have = {p.stem for p in aux.glob("*.parquet")} if aux else None
    gaps = find_gaps(have, expected, empty, aux_have)
    report_gaps(gaps, a.allow_missing)
    out.mkdir(parents=True, exist_ok=True)
    # The spill directory must live outside the published tree: a crash mid-COPY
    # used to leave DuckDB's temp files inside out/, where the catalog's own
    # zone=*/*.parquet globs would walk them.
    tmp_dir = a.tmp_dir / f"merge-{a.year}-{os.getpid()}"
    root, spill = a.out_root.resolve(), a.tmp_dir.resolve()
    if spill == root or root in spill.parents:
        raise SystemExit(f"--tmp-dir {a.tmp_dir} is inside --out-root {a.out_root}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    keep = f"in_utm_zone AND in_mgrs_square AND area_m2 <= {a.max_km2 * 1e6}"

    by_zone: dict[str, list[str]] = {}
    for tk in sorted(have):
        by_zone.setdefault(tk[:2], []).append(tk)
    summary: dict = {
        "year": a.year,
        "max_km2": a.max_km2,
        "filter": keep,
        "source": str(src),
        "aux": str(aux),
        "simplify_tolerance_m": None,
        "provenance": {},
        "spec": None,
        "specs": [],
        "allow_missing": bool(a.allow_missing),
        "missing": gaps,
        "tiles_expected": len(expected - empty),
        "tiles_present": len(have - empty),
        "zones": {},
    }
    prev = out / "_summary.json"
    prev_zones = json.loads(prev.read_text()).get("zones", {}) if prev.is_file() else {}
    t_all = time.perf_counter()
    con = duckdb.connect()
    try:
        con.sql("install spatial; load spatial")
        con.sql(f"set threads={a.threads}; set memory_limit='{a.memory}'")
        con.sql(f"set temp_directory='{tmp_dir}'")
        for z, tiles in by_zone.items():
            t = time.perf_counter()
            files = [src / f"{tk}.parquet" for tk in tiles]
            summary["simplify_tolerance_m"] = (
                summary["simplify_tolerance_m"] or simplify_tolerance(files)
            )
            summary["provenance"] = summary["provenance"] or provenance(files)
            summary["specs"] = sorted(set(summary["specs"]) | outline_specs(files))
            axf = aux_files(aux, tiles)
            if aux and axf:
                check_aux_unique(con, axf)
            part = out / f"zone={z}"
            dst = part / "part-0.parquet"
            fp = fingerprint(files, axf, keep)
            if not a.force and is_current(dst, fp):
                print(f"zone {z}: current, skipped", flush=True)
                # Carry the previous run's counts forward, so a fully-resumed run
                # still writes truthful totals instead of zeroing them.
                summary["zones"][z] = {**prev_zones.get(z, {}), "skipped": True}
                continue
            lst = ", ".join(f"'{p}'" for p in files)
            stats = con.sql(
                f"select count(*), sum(({keep})::int), sum(area_m2)/1e6, "
                f"sum(case when {keep} then area_m2 end)/1e6 from read_parquet([{lst}])"
            ).fetchone()
            assert stats is not None
            n_in, n_out, km2_in, km2_out = (v or 0 for v in stats)
            if not n_out:
                print(f"zone {z}: no retained parcels", flush=True)
                clear_partition(part)
                summary["zones"][z] = {
                    "parcels_in": n_in,
                    "parcels_out": 0,
                    "km2_in": km2_in,
                    "km2_out": 0.0,
                }
                continue
            part.mkdir(exist_ok=True)
            # *.tmp-<pid> and not *.parquet: a temp file named part-0.tmp.parquet
            # inside the published partition is picked up by every zone=*/*.parquet
            # glob, and without the pid two array tasks sharing a zone clobber it --
            # the corruption class main fixed in 7b889a1.
            tmp = part / f"part-0.parquet.tmp-{os.getpid()}"
            try:
                con.sql(
                    f"COPY ({select_sql(files, axf)} WHERE {keep} "
                    f"ORDER BY ST_Hilbert(ST_Centroid(geometry), {WORLD})) "
                    f"TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 50000, "
                    f"KV_METADATA {{{FP_KEY.decode()}: '{fp}'}})"
                )
                written = pq.ParquetFile(tmp).metadata.num_rows
                if written != n_out:
                    # The stats query bypasses the aux join, so it is the only count
                    # that sees fan-out: 3 parcels in once became 6 rows out.
                    raise SystemExit(
                        f"zone {z}: wrote {written:,} rows where the filter retains "
                        f"{n_out:,}; the aux join fanned out and parcel ids would not "
                        "be unique"
                    )
                os.replace(tmp, dst)
            finally:
                Path(tmp).unlink(missing_ok=True)
            dt = time.perf_counter() - t
            summary["zones"][z] = {
                "parcels_in": n_in,
                "parcels_out": n_out,
                "km2_in": km2_in,
                "km2_out": km2_out,
            }
            print(
                f"zone {z}: {n_in:,} -> {n_out:,} parcels, {km2_in:,.0f} -> {km2_out:,.0f} km2, "
                f"{dst.stat().st_size / 1e9:.2f} GB, {dt:.0f}s",
                flush=True,
            )
    finally:
        con.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # One method for the whole year is the normal case; a mixed year (e.g. tiles rerun with a
    # newer method) is stated as such rather than hidden behind one id.
    summary["spec"] = " / ".join(summary["specs"]) or None
    keys = ("parcels_in", "parcels_out", "km2_in", "km2_out")
    counted = [v for v in summary["zones"].values() if all(k in v for k in keys)]
    tot = {k: sum(v[k] for v in counted) for k in keys}
    summary["total"] = tot
    summary["zones_skipped"] = sum(1 for v in summary["zones"].values() if v.get("skipped"))
    summary["seconds"] = time.perf_counter() - t_all
    # Written before the empty-result check: a rerun that retains nothing used to
    # abort with the PREVIOUS run's _summary.json still in place, so an incomplete
    # merge was byte-indistinguishable from a complete one.
    prev.write_text(json.dumps(summary, indent=1))
    if not tot["parcels_out"]:
        raise SystemExit("no retained parcels in any zone")
    print(
        f"total: {tot['parcels_in']:,} -> {tot['parcels_out']:,} parcels, "
        f"{tot['km2_in']:,.0f} -> {tot['km2_out']:,.0f} km2, "
        f"{summary['zones_skipped']} zones skipped, {summary['seconds'] / 60:.0f} min"
    )
    if a.allow_missing and gaps:
        n = sum(len(v) for v in gaps.values())
        print(f"INCOMPLETE: built with --allow-missing over {n} missing input(s)")


if __name__ == "__main__":
    main()
