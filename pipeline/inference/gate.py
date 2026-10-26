"""The raster gate of one year of ``stream.py`` output, run before the upload.

    scan      every tile's tags: completeness against the tile index and keep-list, structure,
              provenance and per-tile statistics; one CSV row per tile
    validate  every tile's file: COG layout, overview levels, grid, overviews recomputed from the
              level above, level means against the inference means, embedded band statistics,
              and for a share of tiles a full decode of the base; one CSV per shard
    summary   ``gate_{year}.json``; exits 1 unless the scan raised no hard flag and every tile
              validated

Scan flags (* fails the gate):
    missing*           an expected tile (keep-list, all four quarters indexed) has no COG
    no_input_unknown*  a keep tile without four quarters that ``--no-mosaic`` does not list
    tmp_leftover*      a ``.tmp-`` or ``.stage-`` file left by a killed writer
    unreadable*        the COG does not open
    structure*         not 2 x uint8 at 2.5 m, 40,032 px, field/boundary, 1/255, five overviews,
                       or tile_key / source_items tags wrong
    no_provenance*     no prov_fingerprint tag
    settings*          barrier off, another nodata fill, patch or overlap, or uncommitted code
    stale*             prov_fingerprint differs from --fingerprint (default: the most common one)
    gpu*               prov_gpu matches none of --gpu
    tf32*              prov_tf32 is not 0
    zero_run*          2,048 all-zero rows (5 km) in a tile whose input is under 50% nodata
    unexpected         a COG for a tile outside the expected set
    top_edge           the top patch row differs from the next by 0.1 or more
    saturated          more than 20% of field pixels at 255
    field_drop         field_mean under half the tile's median in --history CSVs

    python gate.py scan --year 2025 --root out --index-dir index --out qa/scan_2025.csv
    python gate.py validate --year 2025 --root out --fingerprint FP --shard 0 --num-shards 20 \\
        --out qa/validate_2025/shard_0.csv
    python gate.py summary --year 2025 --scan qa/scan_2025.csv --validate-dir qa/validate_2025 \\
        --out qa/gate_2025.json
"""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio

import cog_checks as cc
from predict import OVERLAP, PATCH
from run import OVERVIEWS

HERE = Path(__file__).resolve().parent
KEEP = HERE.parent / "mosaics" / "keeplist.txt"
NO_MOSAIC = HERE / "no_mosaic_tiles.csv"
QUARTERS = "Q1,Q2,Q3,Q4"
HARD = {
    "missing", "no_input_unknown", "tmp_leftover", "unreadable", "structure", "no_provenance",
    "settings", "stale", "gpu", "tf32", "zero_run",
}
ZERO_RUN = 2048  # 5 km of 2.5 m rows
MEAN_TOL = (0.004, 0.05)  # absolute, relative: overview level mean against the inference mean
SIGNAL = 1e-4  # inference field_mean above which every level must hold some field


def cogs(root: Path, year: int) -> dict[str, Path]:
    """tile_key -> COG under raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif."""
    return {p.stem: p for p in (root / "raster" / str(year)).glob("zone=*/gzd=*/*/*.tif")
            if p.parent.name == p.stem}


def leftovers(root: Path, year: int) -> list[tuple[str, int]]:
    d = root / "raster" / str(year)
    return [(str(p), p.stat().st_size)
            for pat in ("zone=*/gzd=*/*/*.tif.tmp-*", "zone=*/gzd=*/*/*.tif.stage-*")
            for p in d.glob(pat)]


def sidecar(root: Path, year: int, tile_key: str) -> dict:
    p = root / "_qa" / str(year) / f"{tile_key}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def tile_quarters(index_dir: Path, year: int) -> dict[str, str]:
    """tile_key -> the quarters ``tile_index_{year}.parquet`` has, e.g. ``"Q3,Q4"``."""
    t = pq.read_table(index_dir / f"tile_index_{year}.parquet", columns=["tile_key", "quarter"])
    qs: dict[str, set[str]] = {}
    for tk, q in zip(t["tile_key"].to_pylist(), t["quarter"].to_pylist(), strict=True):
        qs.setdefault(tk, set()).add(q)
    return {tk: ",".join(sorted(v)) for tk, v in qs.items()}


def expected(index_dir: Path, keep: Path, year: int, known: Path) -> tuple[set, dict, set]:
    """Expected tiles, keep tiles without four quarters (tile -> quarters), and unlisted ones."""
    qs = tile_quarters(index_dir, year)
    keep_set = set(keep.read_text().split())
    absent = {tk: qs.get(tk, "") for tk in keep_set if qs.get(tk) != QUARTERS}
    listed = set()
    if known.exists():
        k = pd.read_csv(known, dtype=str, keep_default_na=False)
        listed = set(k.loc[k.year == str(year), "tile_key"])
    return keep_set - set(absent), absent, set(absent) - listed


def source_items(year: int, tile_key: str) -> str:
    return ",".join(f"Sentinel-2_mosaic_{year}_{q}_{tile_key}" for q in QUARTERS.split(","))


def inspect(path: str, year: int) -> dict:
    """Structure, prov_* and qa_* tags of one COG."""
    tk = Path(path).stem
    rec: dict = {"path": path, "tile_key": tk}
    try:
        with rasterio.open(path) as ds:
            tags = ds.tags()
            bad = []
            if ds.count != 2 or set(ds.dtypes) != {"uint8"}:
                bad.append("bands/dtype")
            if (ds.width, ds.height) != (cc.SIZE, cc.SIZE) or ds.res != (2.5, 2.5):
                bad.append(f"grid {ds.width}x{ds.height} res {ds.res}")
            if ds.descriptions != ("field", "boundary") or ds.nodata is not None:
                bad.append("band names/nodata")
            if any(abs(s - 1 / 255) > 1e-9 for s in ds.scales):
                bad.append(f"scales {ds.scales}")
            if len(ds.overviews(1)) != len(OVERVIEWS):
                bad.append(f"levels={len(ds.overviews(1))}")
            if tags.get("tile_key") != tk:
                bad.append(f"tile_key tag {tags.get('tile_key')}")
            if tags.get("source_items") != source_items(year, tk):
                bad.append("source_items")
            rec["structure"] = "; ".join(bad)
        rec.update({k: v for k, v in tags.items() if k.startswith("prov_")})
        for k, v in tags.items():
            if k.startswith("qa_"):
                try:
                    rec[k[3:]] = float(v)
                except ValueError:
                    rec[k[3:]] = v
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return rec


def _settings(r: pd.Series | dict, a: argparse.Namespace) -> list[str]:
    out = []
    if str(r.get("prov_cuda_sync")) != "1":
        out.append("cuda_sync")
    if str(r.get("prov_nodata_fill")) != a.nodata_fill:
        out.append(f"nodata_fill {r.get('prov_nodata_fill')}")
    if str(r.get("prov_patch")) != str(a.patch):
        out.append(f"patch {r.get('prov_patch')}")
    try:
        same = abs(float(r.get("prov_overlap")) - a.overlap) <= 1e-9  # False for NaN
    except (TypeError, ValueError):
        same = False
    if not same:
        out.append(f"overlap {r.get('prov_overlap')}")
    code = str(r.get("prov_code", "unknown"))
    if code in ("unknown", "nan") or code.endswith("-dirty"):
        out.append(f"code {code}")
    return out


def flag(df: pd.DataFrame, a: argparse.Namespace) -> pd.DataFrame:
    def col(name: str) -> pd.Series:
        return df[name] if name in df else pd.Series(np.nan, index=df.index)

    f: dict[str, pd.Series] = {}
    f["unreadable"] = col("error").notna()
    f["structure"] = ~f["unreadable"] & (col("structure").fillna("") != "")
    fp = col("prov_fingerprint")
    f["no_provenance"] = ~f["unreadable"] & fp.isna()
    want = a.fingerprint or (Counter(fp.dropna()).most_common(1) or [(None,)])[0][0]
    f["stale"] = fp.notna() & (fp != want)
    f["settings"] = fp.notna() & df.apply(lambda r: bool(_settings(r, a)), axis=1)
    if not a.allow_tf32:
        f["tf32"] = fp.notna() & (col("prov_tf32").astype(str) != "0")
    gpu = col("prov_gpu").astype(str)
    f["gpu"] = fp.notna() & ~gpu.apply(lambda g: any(t in g for t in a.gpu))
    f["zero_run"] = (col("zero_row_run") >= ZERO_RUN) & (col("in_nodata_any").fillna(0) < 0.5)
    f["top_edge"] = ((col("top_field") - col("next_field")).abs() >= 0.1) | (
        (col("top_boundary") - col("next_boundary")).abs() >= 0.1)
    f["saturated"] = col("sat255") > 0.2
    if a.history:
        hist = pd.concat([pd.read_csv(h)[["tile_key", "field_mean"]] for h in a.history])
        med = df.tile_key.map(hist.groupby("tile_key").field_mean.median())
        f["field_drop"] = (med > 0.05) & (col("field_mean") < 0.5 * med)
    flags = pd.DataFrame(f).fillna(False).astype(bool)
    df["qa_flags"] = flags.apply(lambda r: ",".join(k for k, v in r.items() if v), axis=1)
    df["fingerprint_expected"] = want
    return df


def scan(a: argparse.Namespace) -> int:
    want, absent, unknown = expected(a.index_dir, a.keep_list, a.year, a.no_mosaic)
    if absent:
        print(f"{a.year}: {len(absent)} keep tiles without four quarters, not expected: "
              + ", ".join(f"{t} [{q or 'none'}]" for t, q in sorted(absent.items())))
    paths = cogs(a.root, a.year)
    tmp = leftovers(a.root, a.year)
    with ThreadPoolExecutor(a.workers) as ex:
        recs = list(ex.map(lambda p: inspect(str(p), a.year), sorted(paths.values())))
    df = pd.DataFrame(recs) if recs else pd.DataFrame({"tile_key": [], "path": []})
    df = flag(df, a)
    df.loc[~df.tile_key.isin(want) & (df.qa_flags == ""), "qa_flags"] = "unexpected"
    extra = (
        [{"tile_key": t, "path": "", "qa_flags": "missing"} for t in sorted(want - set(paths))]
        + [{"tile_key": t, "path": "", "qa_flags": "no_input_unknown"} for t in sorted(unknown)]
        + [{"tile_key": Path(p).name, "path": p, "tmp_bytes": s, "qa_flags": "tmp_leftover"}
           for p, s in tmp]
    )
    if extra:
        df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    df["year"] = a.year
    counts = Counter(f for fl in df.qa_flags for f in fl.split(",") if f)
    print(f"{a.year}: {len(paths)} COGs, {len(want)} expected")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        ex_ = df[df.qa_flags.str.contains(k)].tile_key.head(5).tolist()
        print(f"  {'HARD' if k in HARD else 'warn'} {k:16s} {v:6d}  e.g. {ex_}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.out, index=False)
    return 1 if any(k in HARD for k in counts) else 0


def _grid_errors(ds: rasterio.DatasetReader, tk: str, inf: dict) -> list[str]:
    err = []
    if ds.crs is None or ds.crs.to_epsg() != cc.expected_epsg(tk):
        err.append(f"crs {ds.crs} != EPSG:{cc.expected_epsg(tk)}")
    t = inf.get("in", {}).get("transform")
    if not t:
        return [*err, "no input transform in the sidecar"]
    a, b, c, d, e, f = (float(v) for v in t.split(","))
    want = (a / 4, b, c, d, e / 4, f)
    got = tuple(ds.transform)[:6]
    if any(abs(g - w) > 0.01 for g, w in zip(got, want, strict=True)):
        err.append(f"transform {got} != input grid / 4 {want}")
    return err


def _level_errors(stats: list[dict], out: dict) -> list[str]:
    if "field_mean" not in out:
        return ["no output statistics in the sidecar"]
    err = []
    for s in stats:
        for band in ("field", "boundary"):
            want, got = out[f"{band}_mean"], s[f"{band}_mean"]
            if abs(got - want) > MEAN_TOL[0] + MEAN_TOL[1] * want:
                err.append(f"level {s['factor']} {band} mean {got:.5f} != base {want:.5f}")
        if out["field_mean"] > SIGNAL and s["nonzero"] == 0:
            err.append(f"level {s['factor']} empty while the base has signal")
        base = out.get("zero_row_run", 0)
        if s["zero_row_run"] >= ZERO_RUN and s["zero_row_run"] > 2 * base + 2 * s["factor"]:
            err.append(f"level {s['factor']} zero-row run {s['zero_row_run']} > base {base}")
    return err


def validate_one(path: str, a: argparse.Namespace, full: bool) -> dict:
    p = Path(path)
    tk = p.stem
    rec: dict = {"tile_key": tk, "path": path, "bytes": p.stat().st_size, "full_decode": full}
    err: list[str] = []
    try:
        r = inspect(path, a.year)
        if r.get("error") or r.get("structure"):
            err.append(f"structure: {r.get('error') or r['structure']}")
        if r.get("prov_fingerprint") != a.fingerprint:
            err.append(f"prov_fingerprint {r.get('prov_fingerprint')}")
        err += [f"settings: {s}" for s in _settings(r, a)]
        if not a.allow_tf32 and str(r.get("prov_tf32")) != "0":
            err.append(f"prov_tf32 {r.get('prov_tf32')}")
        if not any(g in str(r.get("prov_gpu", "")) for g in a.gpu):
            err.append(f"prov_gpu {r.get('prov_gpu')}")
        inf = sidecar(a.root, a.year, tk)
        if inf.get("prov", {}).get("fingerprint") != a.fingerprint:
            err.append("sidecar missing or from another run")
        with rasterio.open(path) as ds:
            err += cc.layout_errors(ds, rec["bytes"])
            err += _grid_errors(ds, tk, inf)
        err += cc.level_errors(path)
        if not err:  # pixel checks only on a sound file
            rec["ovr_max_diff"], e = cc.overview_chain(path, cc.pick_windows(path, seed=a.seed))
            err += e
            stats = cc.level_stats(path)
            rec.update({f"l{s['factor']}_nonzero": s["nonzero"] for s in stats})
            err += _level_errors(stats, inf.get("out", {}))
            emb = cc.embedded_stats(path)
            missing = [i for i, t in enumerate(emb, 1) if any(k not in t for k in cc.STAT_KEYS)]
            if missing:
                err.append(f"no embedded band statistics {missing}")
            if Path(f"{path}.aux.xml").exists():
                err.append("aux.xml sidecar")
            if full and not missing:
                base = cc.base_means(path)
                rec["base_field_mean"] = base["field_mean"]
                for i, band in enumerate(("field", "boundary")):
                    if abs(base[f"{band}_mean"] - inf["out"][f"{band}_mean"]) > 1e-6:
                        err.append(f"base {band} mean {base[f'{band}_mean']:.6f} != inference")
                    if abs(float(emb[i]["STATISTICS_MEAN"]) - base[f"{band}_mean"] * 255) > 1e-6:
                        err.append(f"band {i + 1} STATISTICS_MEAN != decoded mean")
    except Exception as e:
        err.append(f"error: {type(e).__name__}: {str(e)[:120]}")
    rec["errors"] = "; ".join(err)
    return rec


def size_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Fail a COG outside 0.05-10 x the year's median size; warn outside 0.2-5 x."""
    r = df.bytes / df.bytes.median()
    df["size_ratio"] = r
    hard = (r < 0.05) | (r > 10)
    sep = df.loc[hard, "errors"].where(df.loc[hard, "errors"] == "", df.loc[hard, "errors"] + "; ")
    df.loc[hard, "errors"] = sep + "size " + r[hard].round(3).astype(str) + " x median"
    df["size_warn"] = (r < 0.2) | (r > 5)
    return df


def validate(a: argparse.Namespace) -> int:
    paths = sorted(cogs(a.root, a.year).values())[a.shard :: a.num_shards]
    if not paths:
        sys.exit(f"no COGs under {a.root / 'raster' / str(a.year)}")
    rng = np.random.default_rng(a.seed + a.year)
    full = [rng.random() < a.full_frac for _ in paths]
    with ProcessPoolExecutor(a.workers) as ex:
        recs = list(ex.map(validate_one, map(str, paths), [a] * len(paths), full, chunksize=1))
    df = size_flags(pd.DataFrame(recs))
    bad = df[df.errors != ""]
    diff = int(df.ovr_max_diff.max()) if "ovr_max_diff" in df and df.ovr_max_diff.notna().any() \
        else "n/a"
    print(f"{a.year}: {len(df)} COGs, {int(df.full_decode.sum())} fully decoded, {len(bad)} "
          f"failed, overview max diff {diff}, {int(df.size_warn.sum())} size warnings")
    for _, r in bad.head(20).iterrows():
        print(f"  {r.tile_key}: {r.errors}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.out, index=False)
    return 1 if len(bad) else 0


def summary(a: argparse.Namespace) -> int:
    qa = pd.read_csv(a.scan, low_memory=False)
    shards = sorted(a.validate_dir.glob("*.csv"))
    val = pd.concat([pd.read_csv(p) for p in shards], ignore_index=True)
    flags = Counter(f for fl in qa.qa_flags.fillna("") for f in str(fl).split(",") if f)
    tiles = qa[qa.path.fillna("").astype(str).str.endswith(".tif")]
    s: dict = {
        "year": a.year,
        "tiles": len(tiles),
        "qa_flags": dict(flags),
        "hard_flags": sorted(k for k in flags if k in HARD),
        "fingerprints": tiles.prov_fingerprint.dropna().unique().tolist()
        if "prov_fingerprint" in tiles else [],
        "gpu": tiles.prov_gpu.astype(str).str.extract(r"(H100|A100)")[0].value_counts().to_dict()
        if "prov_gpu" in tiles else {},
        "field_mean_median": float(tiles.field_mean.median()) if "field_mean" in tiles else None,
        "validate": {
            "shards": len(shards),
            "cogs": len(val),
            "failed": int((val.errors.fillna("") != "").sum()),
            "full_decoded": int(val.full_decode.sum()),
            "overview_max_diff": float(val.ovr_max_diff.max()) if "ovr_max_diff" in val else None,
            "size_warn": int(val.size_warn.sum()),
            "bytes": int(val.bytes.sum()),
            "covers_all_tiles": set(val.tile_key) == set(tiles.tile_key),
        },
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if a.release and a.release.exists():
        rel = pd.read_csv(a.release)
        m = tiles.merge(rel[["tile_key", "field_mean"]], on="tile_key", suffixes=("", "_rel"))
        r = m.field_mean / m.field_mean_rel
        s["release"] = {
            "tiles_compared": len(m),
            "field_mean_ratio_p05_p50_p95": [float(r.quantile(q)) for q in (0.05, 0.5, 0.95)],
            "tiles_ratio_below_0.5": int((r < 0.5).sum()),
            "tiles_ratio_above_2": int((r > 2).sum()),
        }
    s["pass"] = bool(not s["hard_flags"] and len(s["fingerprints"]) == 1
                     and s["validate"]["failed"] == 0 and s["validate"]["covers_all_tiles"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(s, indent=1, default=str))
    print(json.dumps(s, indent=1, default=str))
    return 0 if s["pass"] else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    workers = int(os.environ.get("SLURM_CPUS_PER_TASK", 4))
    for name in ("scan", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--year", type=int, required=True)
        p.add_argument("--root", type=Path, required=True, help="stream.py --out-root")
        p.add_argument("--gpu", nargs="+", default=["H100", "A100"],
                       help="accepted prov_gpu substrings")
        p.add_argument("--nodata-fill", default="window31")
        p.add_argument("--patch", type=int, default=PATCH)
        p.add_argument("--overlap", type=float, default=OVERLAP)
        p.add_argument("--allow-tf32", action="store_true")
        p.add_argument("--workers", type=int, default=workers)
        p.add_argument("--out", type=Path)
    sc = sub.choices["scan"]
    sc.add_argument("--index-dir", type=Path, required=True)
    sc.add_argument("--keep-list", type=Path, default=KEEP)
    sc.add_argument("--no-mosaic", type=Path, default=NO_MOSAIC,
                    help="keep tiles known to have no mosaic in some quarters")
    sc.add_argument("--fingerprint", help="expected prov_fingerprint (default: the most common)")
    sc.add_argument("--history", nargs="*", help="QA CSVs of other years, for field_drop")
    va = sub.choices["validate"]
    va.add_argument("--fingerprint", required=True)
    va.add_argument("--full-frac", type=float, default=0.1, help="share of tiles fully decoded")
    va.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    va.add_argument("--num-shards", type=int,
                    default=int(os.environ.get("SLURM_ARRAY_TASK_COUNT", 1)))
    va.add_argument("--seed", type=int, default=0)
    su = sub.add_parser("summary")
    su.add_argument("--year", type=int, required=True)
    su.add_argument("--scan", type=Path, required=True)
    su.add_argument("--validate-dir", type=Path, required=True)
    su.add_argument("--release", type=Path, help="a release QA CSV of the same year (optional)")
    su.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    sys.exit({"scan": scan, "validate": validate, "summary": summary}[a.step](a))


if __name__ == "__main__":
    main()
