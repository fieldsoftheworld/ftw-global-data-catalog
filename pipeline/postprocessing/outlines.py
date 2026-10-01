"Run the fbp package on score windows and write parcel GeoParquet."

import argparse
import json
import math
import os
import resource
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


for _v in (
    "OMP_NUM_THREADS",
    "NUMBA_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS",
    "GDAL_NUM_THREADS",
):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
# No CPL_VSIL_CURL_ALLOWED_EXTENSIONS here: remote QA hrefs are not all ``*.tif``
# (CDSE serves ``.../Nodes(B04.tif)/$value``) and an extension allow-list makes GDAL
# refuse them before any request. Remote reads carry their own options; see
# terrain.VSICURL_OPTS.

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import rasterio.features
import shapely
from pyproj import CRS, Transformer
from rasterio.windows import Window
from scipy import ndimage as ndi

from context import aux_rasters
from pool_utils import drain, exit_on_failures

PX_M2 = 6.25


def score_labels(u8, method):
    pf, pb = u8.astype(np.float32) / 255
    prob = np.stack([np.clip(1 - pf - pb, 0, 1), pf, pb]).astype(np.float16)
    return method(prob.argmax(0).astype(np.uint8), prob, px_m2=PX_M2).astype(np.int32)


SPEC = "nbg-pb-h0.01-t0.3+R35+F10+G2+A900"


def method_for(backend: str):
    "``parse(SPEC)`` on the chosen backend: ``fast`` appends ``+q1``, ``exact`` is skimage."
    from fbp.methods import parse

    return parse(SPEC + ("+q1" if backend == "fast" else ""))


REPORT_EVERY = 50


def _windows(size: int, core: int, halo: int):
    for c0 in range(0, size, core):
        c1 = min(size, c0 + core)
        yield c0, c1, max(0, c0 - halo), min(size, c1 + halo)


def _rss_gb() -> float:
    "Current resident set of this process in GB (from /proc; ru_maxrss only ever grows)."
    if not Path("/proc/self/statm").exists():
        return 0.0
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * resource.getpagesize() / 1e9


class _RssSampler:
    "Background thread sampling resident memory every 50 ms, attributed to the current stage."

    def __init__(self) -> None:
        import threading

        self.stage = "setup"
        self.peaks: dict[str, float] = {}
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            v = _rss_gb()
            if v > self.peaks.get(self.stage, 0.0):
                self.peaks[self.stage] = v

    def close(self, prof: dict) -> None:
        self._stop.set()
        self._t.join()
        for k, v in self.peaks.items():
            prof[f"rss_{k}_gb"] = round(v, 2)


def _probe(prof: dict, stage: str) -> None:
    "Mark the start of `stage` for the sampler stored in prof (no-op without one)."
    s = prof.get("_sampler")
    if s is not None:
        s.stage = stage


def process_tile(
    path: Path,
    year: int,
    out_dir: Path,
    index: Path,
    core: int,
    halo: int,
    backend: str,
    simplify_m: float = 0.0,
) -> dict:
    "Stage the tile's COG locally if the scratch copy is gone, run it, clean up."
    if not path.is_file():
        raise FileNotFoundError(path)
    prof = _run_tile(str(path), path.stem, year, out_dir, index, core, halo, backend, simplify_m)
    prof["t_stage"] = 0.0
    return prof


def _run_tile(
    path: str,
    tk: str,
    year: int,
    out_dir: Path,
    index: Path,
    core: int,
    halo: int,
    backend: str,
    simplify_m: float,
) -> dict:
    from polygons import simplify_coverage

    method = method_for(backend)
    prof = {
        "tile_key": tk,
        "spec": method.id,
        "backend": backend,
        "core": core,
        "halo": halo,
        "t_read": 0.0,
        "t_bv": 0.0,
        "t_attr": 0.0,
        "t_poly": 0.0,
        "t_simplify": 0.0,
        "windows": 0,
    }
    prof["_sampler"] = _RssSampler()
    t_all = time.perf_counter()
    with rasterio.open(path) as ds:
        if ds.count != 2 or ds.dtypes != ("uint8", "uint8"):
            raise ValueError("expected two uint8 probability bands")
        crs, tr, H, W = ds.crs, ds.transform, ds.height, ds.width
    t = time.perf_counter()
    if not np.isclose(tr.a, 2.5) or not np.isclose(tr.e, -2.5) or tr.b != 0 or tr.d != 0:
        raise ValueError("expected north-up 2.5 m score grid")
    aux = aux_rasters(tk, year, index, crs, tr * tr.scale(4), (H // 4, W // 4))
    prof["t_aux"] = time.perf_counter() - t
    zone = CRS(crs).utm_zone
    zn = int(zone[:-1])
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    sq_x0 = math.floor((tr.c + 5000) / 100000) * 100000
    sq_y1 = math.ceil((tr.f - 5000) / 100000) * 100000

    rows, geoms = [], []
    pid = 0
    for r0, r1, R0, R1 in _windows(H, core, halo):
        for c0, c1, C0, C1 in _windows(W, core, halo):
            prof["windows"] += 1
            t = time.perf_counter()
            _probe(prof, "read")
            with rasterio.open(path) as ds:
                u8 = ds.read(window=Window(C0, R0, C1 - C0, R1 - R0))
            prof["t_read"] += time.perf_counter() - t
            if not u8.any():
                continue
            t = time.perf_counter()
            _probe(prof, "bv")
            inst = score_labels(u8, method)
            ncrop = 1
            prof["crops"] = prof.get("crops", 0) + ncrop
            prof["t_bv"] += time.perf_counter() - t
            _probe(prof, "attr")
            if not inst.any():
                continue

            t = time.perf_counter()
            n_id = int(inst.max()) + 1
            m = inst > 0
            idx = inst[m]
            rr, cc = np.nonzero(m)
            cnt = np.bincount(idx, minlength=n_id).astype(np.float64)
            with np.errstate(invalid="ignore", divide="ignore"):
                cy = np.bincount(idx, rr, n_id) / cnt
                cx = np.bincount(idx, cc, n_id) / cnt

            own = (cnt > 0) & (cy + R0 >= r0) & (cy + R0 < r1) & (cx + C0 >= c0) & (cx + C0 < c1)
            own[0] = False
            ids = np.nonzero(own)[0]
            if len(ids) == 0:
                prof["t_attr"] += time.perf_counter() - t
                continue

            edge = np.zeros(inst.shape, bool)
            if R0 > 0:
                edge[0, :] = True
            if R1 < H:
                edge[-1, :] = True
            if C0 > 0:
                edge[:, 0] = True
            if C1 < W:
                edge[:, -1] = True
            touch = np.bincount(inst[edge & m], minlength=n_id) > 0
            sl = ndi.find_objects(inst)

            def mean_of(vals, idx=idx, cnt=cnt, n_id=n_id):
                with np.errstate(invalid="ignore", divide="ignore"):
                    return np.bincount(idx, vals, n_id) / cnt

            R, C = rr + R0, cc + C0
            q16 = aux["nod"][
                np.minimum(R // 16, aux["nod"].shape[0] - 1),
                np.minimum(C // 16, aux["nod"].shape[1] - 1),
            ]
            a30 = (
                np.minimum(R // 12, aux["dem"].shape[0] - 1),
                np.minimum(C // 12, aux["dem"].shape[1] - 1),
            )
            attrs = {
                "pf_mean": mean_of(u8[0][m] / 255.0),
                "pb_mean": mean_of(u8[1][m] / 255.0),
                "frac_nodata_1q": mean_of(q16 >= 1),
                "frac_nodata_3q": mean_of(q16 >= 3),
                "frac_water": mean_of(aux["water"][a30]),
                "frac_crops_ever": mean_of(aux["crops"][a30]),
                "slope_mean": mean_of(np.nan_to_num(aux["slope"][a30])),
                "frac_slope_gt30": mean_of(np.nan_to_num(aux["slope"][a30]) > 30),
                "elev_mean": mean_of(aux["dem"][a30]),
            }
            del R, C, q16, a30, rr, cc, idx
            prof["t_attr"] += time.perf_counter() - t
            _probe(prof, "poly")

            t = time.perf_counter()
            lut = np.zeros(n_id, np.int32)
            lut[ids] = ids
            owned = lut[inst]
            wtr = tr * rasterio.Affine.translation(C0, R0)
            parts: dict[int, list] = {}
            for g, v in rasterio.features.shapes(owned, mask=owned > 0, transform=wtr):
                parts.setdefault(int(v), []).append(shapely.geometry.shape(g))
            for i in ids:
                ps = parts.get(int(i))
                if not ps:
                    continue
                geom = ps[0] if len(ps) == 1 else shapely.MultiPolygon(ps)
                geoms.append(geom)
                s = sl[i - 1]
                ex, ey = tr * (cx[i] + C0 + 0.5, cy[i] + R0 + 0.5)
                lon, _lat = to_ll.transform(ex, ey)
                pid += 1
                rows.append(
                    {
                        "tile_key": tk,
                        "year": year,
                        "parcel_id": pid,
                        "area_m2": float(cnt[i]) * PX_M2,
                        "n_parts": len(ps),
                        "ext_h_px": s[0].stop - s[0].start,
                        "ext_w_px": s[1].stop - s[1].start,
                        "touches_window_edge": bool(touch[i]),
                        "in_utm_zone": bool(-180 + 6 * (zn - 1) <= lon < -180 + 6 * zn),
                        "in_mgrs_square": bool(
                            sq_x0 <= ex < sq_x0 + 100000 and sq_y1 - 100000 < ey <= sq_y1
                        ),
                        **{k: float(v[i]) for k, v in attrs.items()},
                    }
                )
            prof["t_poly"] += time.perf_counter() - t
            _probe(prof, "loop")
            del inst, owned, u8

    t = time.perf_counter()
    out = out_dir / f"{tk}.parquet"
    if rows:
        proj = simplify_coverage(np.array(geoms, dtype=object), simplify_m)
        prof["t_simplify"] = time.perf_counter() - t
        t = time.perf_counter()
        g = shapely.transform(
            proj,
            lambda xy: np.column_stack(to_ll.transform(xy[:, 0], xy[:, 1])),
        )
        b = shapely.bounds(g)
        tbl = pa.Table.from_pylist(rows)
        for j, k in enumerate(("xmin", "ymin", "xmax", "ymax")):
            tbl = tbl.append_column(k, pa.array(b[:, j]))
        tbl = tbl.append_column("geometry", pa.array(shapely.to_wkb(g)))
        meta = {
            b"geo": (
                b'{"version":"1.1.0","primary_column":"geometry","columns":{"geometry":'
                b'{"encoding":"WKB","geometry_types":["Polygon","MultiPolygon"],'
                b'"crs":' + CRS("EPSG:4326").to_json().encode() + b"}}}"
            )
        }
        tbl = tbl.replace_schema_metadata({**(tbl.schema.metadata or {}), **meta})
        tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
        pq.write_table(tbl, tmp, compression="zstd")
        os.replace(tmp, out)
    else:
        schema = pa.schema(
            [
                pa.field(k, typ)
                for k, typ in {
                    "tile_key": pa.string(),
                    "year": pa.int64(),
                    "parcel_id": pa.int64(),
                    "area_m2": pa.float64(),
                    "n_parts": pa.int64(),
                    "ext_h_px": pa.int64(),
                    "ext_w_px": pa.int64(),
                    "touches_window_edge": pa.bool_(),
                    "in_utm_zone": pa.bool_(),
                    "in_mgrs_square": pa.bool_(),
                    **{
                        k: pa.float64()
                        for k in [
                            "pf_mean",
                            "pb_mean",
                            "frac_nodata_1q",
                            "frac_nodata_3q",
                            "frac_water",
                            "frac_crops_ever",
                            "slope_mean",
                            "frac_slope_gt30",
                            "elev_mean",
                            "xmin",
                            "ymin",
                            "xmax",
                            "ymax",
                        ]
                    },
                    "geometry": pa.binary(),
                }.items()
            ]
        )
        tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
        geo = {
            "version": "1.1.0",
            "primary_column": "geometry",
            "columns": {
                "geometry": {
                    "encoding": "WKB",
                    "geometry_types": ["Polygon", "MultiPolygon"],
                    "crs": json.loads(CRS("EPSG:4326").to_json()),
                }
            },
        }
        schema = schema.with_metadata({b"geo": json.dumps(geo).encode()})
        pq.write_table(pa.Table.from_pylist([], schema=schema), tmp, compression="zstd")
        os.replace(tmp, out)
    prof["t_write"] = time.perf_counter() - t
    prof["t_total"] = time.perf_counter() - t_all
    prof["parcels"] = len(rows)
    prof["parcels_touch_edge"] = int(sum(r["touches_window_edge"] for r in rows))
    prof["max_ext_px"] = int(max((max(r["ext_h_px"], r["ext_w_px"]) for r in rows), default=0))
    prof["out_bytes"] = out.stat().st_size if rows else 0
    prof.pop("_sampler").close(prof)
    prof["peak_rss_gb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    return prof


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--scores", type=Path, default=Path("scores"))
    ap.add_argument("--out-root", type=Path, default=Path("outlines"))
    ap.add_argument("--index-dir", type=Path, default=Path("index"))
    ap.add_argument("--tiles", nargs="*", default=None)
    ap.add_argument("--tile-list", type=Path, default=None)
    ap.add_argument("--core", type=int, default=8192)
    ap.add_argument("--halo", type=int, default=512)
    ap.add_argument("--backend", choices=("fast", "exact"), default="exact")
    ap.add_argument(
        "--simplify-m",
        type=float,
        default=0.0,
        help="coverage-simplify tolerance, metres (default off; use simplify_polygons.py)",
    )
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument(
        "--num-shards", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_COUNT", 1))
    )
    ap.add_argument("--profile-out", type=Path, default=None)
    ap.add_argument("--verbose", action="store_true", help="print per-tile timings and metrics")
    a = ap.parse_args()

    src = a.scores / str(a.year)
    names = a.tiles or (a.tile_list.read_text().split() if a.tile_list else None)
    paths = [src / f"{n}.tif" for n in names] if names else sorted(src.glob("*.tif"))
    if (
        a.core <= 0
        or a.halo < 0
        or a.workers <= 0
        or a.num_shards < 1
        or not 0 <= a.shard < a.num_shards
    ):
        ap.error("invalid window, workers or shard")
    if not paths:
        ap.error("no input scores")
    paths = paths[a.shard :: a.num_shards]
    if a.scores.resolve() == a.out_root.resolve():
        ap.error("score and outline directories must differ")
    out_dir = a.out_root / str(a.year)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [p for p in paths if not (out_dir / f"{p.stem}.parquet").exists()]

    index = sorted(a.index_dir.glob(f"tile_index_{a.year}*.parquet"))
    if paths and not index:
        ap.error("no quarterly source index")
    print(
        f"{len(paths)} tiles, backend={a.backend}, core={a.core} halo={a.halo}, "
        f"workers={a.workers}",
        flush=True,
    )
    profs = []
    done = 0

    def report(tk: str, pr: dict) -> None:
        nonlocal done
        profs.append(pr)
        done += 1
        if a.verbose:
            print(
                f"  {tk}: {pr['t_total']:.0f}s (stage {pr['t_stage']:.0f} aux {pr['t_aux']:.0f} "
                f"read {pr['t_read']:.0f} bv {pr['t_bv']:.0f} attr {pr['t_attr']:.0f} poly "
                f"{pr['t_poly']:.0f} write {pr['t_write']:.0f}) {pr['parcels']:,} parcels, "
                f"{pr['parcels_touch_edge']} edge, max ext {pr['max_ext_px']} px, "
                f"{pr['out_bytes'] / 1e6:.1f} MB, RSS {pr['peak_rss_gb']:.1f} GB",
                flush=True,
            )
        elif done % REPORT_EVERY == 0:
            print(f"  {done}/{len(paths)} tiles done", flush=True)

    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1) as pool:
        futs = {
            pool.submit(
                process_tile, p, a.year, out_dir, index, a.core, a.halo, a.backend, a.simplify_m
            ): p.stem
            for p in paths
        }
        failures = drain(futs, report)
    if profs:
        if a.profile_out:
            pq.write_table(pa.Table.from_pylist(profs), a.profile_out)
        n = len(profs)
        tt = sum(p["t_total"] for p in profs)
        pc = sum(p["parcels"] for p in profs)
        rss = max(p["peak_rss_gb"] for p in profs)
        print(f"{n} tiles ok, {pc:,} parcels, {tt / 3600:.1f} tile-h, peak RSS {rss:.1f} GB")
    exit_on_failures(failures, len(paths), "tiles")


if __name__ == "__main__":
    main()
