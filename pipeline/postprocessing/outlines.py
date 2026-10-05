"Run the fbp package on score windows and write parcel GeoParquet."

import argparse
import json
import multiprocessing
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
from pool_utils import drain, exit_on_failures, retry_io_failures

PX_M2 = 6.25

#: ``max_tasks_per_child`` silently selects the **spawn** context (measured: a plain
#: ProcessPoolExecutor gets ForkContext, one with max_tasks_per_child=1 gets
#: SpawnContext), so the choice is made explicit here rather than inferred. Spawn is
#: what we want: a fresh process per tile is the point of max_tasks_per_child=1, the
#: workers start an RSS-sampling thread and forking a threaded parent is a known
#: hazard, and a spawned child re-imports this module, which is where the
#: single-thread BLAS/GDAL environment is set.
MP_CONTEXT = multiprocessing.get_context("spawn")


def score_labels(u8, method):
    pf, pb = u8.astype(np.float32) / 255
    prob = np.stack([np.clip(1 - pf - pb, 0, 1), pf, pb]).astype(np.float16)
    return method(prob.argmax(0).astype(np.uint8), prob, px_m2=PX_M2).astype(np.int32)


SPEC = "nbg-pb-h0.01-t0.3+R35+F10+G2+A900"


def method_for(backend: str):
    """``parse(SPEC)`` on the chosen backend: ``fast`` appends ``+q1``, ``exact`` is skimage.

    ``fbp`` is not in requirements.txt and cannot be: it is not on PyPI, and the
    unrelated ``fbp`` 1.3.6 that IS on PyPI does not expose ``fbp.methods``. One
    clear failure here beats 10k identical ImportError tracebacks from the workers.
    """
    try:
        from fbp.methods import parse
    except ImportError as exc:
        raise SystemExit(
            "the private fbp package is required for the outline stage and is not "
            f"importable ({exc}). It is NOT the unrelated 'fbp' on PyPI; install it "
            "from its own source. simplify_polygons, merge_polygons and "
            "fiboa_convert do not need it."
        ) from exc

    return parse(SPEC + ("+q1" if backend == "fast" else ""))


#: Wait before the one retry pass over tiles that failed on a remote read.
IO_RETRY_WAIT_S = 60
REPORT_EVERY = 50


#: Side of an MGRS grid square, metres.
MGRS_SQUARE_M = 100_000.0
#: A tile's square must sit inside its raster; allow this much slack (the published rasters
#: overhang the square by 0-80 m, and a padded raster by its pad).
MGRS_FIT_TOLERANCE_M = 250.0


def lon_in_zone(zn: int, band: str, lon: float) -> bool:
    """Is ``lon`` inside UTM zone ``zn`` on the grid the Sentinel-2 tiles use?

    Band V (56-64N) is not on the nominal 6-degree pitch: 32V is widened to 3-12E
    and 31V narrowed to 0-3E, the convention MGRS and the Sentinel-2 tiling grid
    both follow. Testing 32V against the nominal 6-12E band rejects every parcel
    between 3E and 6E -- Bergen 5.32, Stavanger 5.73, Jaeren 5.60 -- and because
    no 31V tile covers 3-6E those parcels are lost outright rather than claimed by
    a neighbour. Band V lies well inside this catalog's 65.82N extent.

    Band X (72-84N) has its own, different exceptions (32X/34X/36X do not exist)
    and begins above that extent, so it is rejected rather than guessed at.
    """
    if band == "X":
        raise ValueError(f"zone {zn}{band}: band X UTM exceptions are not implemented")
    if band == "V" and zn in (31, 32):
        w, e = (0.0, 3.0) if zn == 31 else (3.0, 12.0)
    else:
        w, e = -180.0 + 6 * (zn - 1), -180.0 + 6 * zn
    return w <= lon < e


def mgrs_square(tr, height: int, width: int) -> tuple[float, float, float, float]:
    """The tile's own 100 km MGRS square as (x0, y0, x1, y1), from the raster's actual extent.

    The published score rasters are 100.08 km (40,032 px x 2.5 m) with their origin 0-80 m
    off the square, so the square is the 100 km grid cell that the raster's north-west corner
    snaps to: ``round(origin / 100 km)``. Rounding to the *nearest* cell is stable for any
    offset or padding under 50 km, which a floor/ceil of ``origin +/- pad`` is not (a sub-pixel
    offset flips floor/ceil by a whole 100 km at the exact boundary the origin sits on).

    An earlier version took ``origin + 5 km`` and ``extent - 5 km`` as the square, which
    assumes a 110 km raster with a 5 km pad. On the real 100.08 km rasters that claims a
    90.08 km square (81% of the true area): the outer ~5 km frame of every tile is owned by
    nobody and silently dropped. The square is checked against the raster extent so a
    raster that cannot contain it fails loudly instead of owning a wrong square.
    """
    x0 = round(tr.c / MGRS_SQUARE_M) * MGRS_SQUARE_M
    y1 = round(tr.f / MGRS_SQUARE_M) * MGRS_SQUARE_M
    x1, y0 = x0 + MGRS_SQUARE_M, y1 - MGRS_SQUARE_M
    west, north = tr.c, tr.f
    east, south = tr.c + width * abs(tr.a), tr.f - height * abs(tr.e)
    tol = MGRS_FIT_TOLERANCE_M
    if x0 < west - tol or y1 > north + tol or x1 > east + tol or y0 < south - tol:
        raise ValueError(
            f"raster extent x {west:.0f}-{east:.0f}, y {south:.0f}-{north:.0f} does not contain "
            f"the 100 km MGRS square x {x0:.0f}-{x1:.0f}, y {y0:.0f}-{y1:.0f} its origin snaps to"
        )
    return x0, y0, x1, y1


def slope_attrs(slope_px: np.ndarray, mean_of) -> tuple[np.ndarray, np.ndarray]:
    """``slope_mean`` and ``frac_slope_gt30``, NaN together where the DEM is absent.

    Where the DEM has no coverage, slope is UNKNOWN, not flat. ``np.nan_to_num``
    turned missing DEM into slope 0.0 while ``elev_mean`` stayed NaN, so a coastal
    parcel with no DEM at all reported a perfectly flat 0 degrees and passed every
    "is this flat enough" QA filter. ``np.bincount`` propagates NaN, so simply not
    filling makes ``slope_mean`` NaN exactly where ``elev_mean`` is; the steep
    fraction is masked to match rather than counting an unknown as "not steep".
    """
    slope_mean = mean_of(slope_px)
    with np.errstate(invalid="ignore"):
        steep = mean_of((slope_px > 30).astype(np.float64))
    return slope_mean, np.where(np.isnan(slope_mean), np.nan, steep)


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


FP_KEY = b"outline_fingerprint"
PROVENANCE_KEY = b"outline_provenance"

#: The per-tile schema, declared once and used for the empty and non-empty cases
#: alike. ``pa.Table.from_pylist(rows)`` inferred it from the rows, so the column
#: ORDER followed insertion and a column that happened to be all-NULL in one tile
#: (slope_mean on a tile with no DEM, say) came out a different type there --
#: tiles written by different workers then disagreed, and a whole-year read of
#: them fails or silently casts.
SCHEMA = pa.schema(
    [
        pa.field("tile_key", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("parcel_id", pa.int64()),
        pa.field("area_m2", pa.float64()),
        pa.field("n_parts", pa.int64()),
        pa.field("ext_h_px", pa.int64()),
        pa.field("ext_w_px", pa.int64()),
        pa.field("touches_window_edge", pa.bool_()),
        pa.field("in_utm_zone", pa.bool_()),
        pa.field("in_mgrs_square", pa.bool_()),
        *(
            pa.field(k, pa.float64())
            for k in (
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
            )
        ),
        pa.field("geometry", pa.binary()),
    ]
)

#: The GeoParquet footer DuckDB needs to read ``geometry`` as GEOMETRY downstream.
GEO_META = json.dumps(
    {
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
).encode()


def fingerprint(src: Path, year: int, core: int, halo: int, backend: str, simplify_m: float) -> str:
    """Identity of the source COG plus every flag that changes the output.

    Resume used to test only that the output file exists, so a regenerated score
    COG or a changed --core/--halo/--backend/--simplify-m silently published the
    previous run's parcels. Built like the siblings: PR2's run.py
    inference_fingerprint (size, mtime, model hash, flags) and
    simplify_polygons.simplify_fingerprint (tol, size, mtime). The COG's own
    inference_fingerprint tag is preferred when PR2 stamped one, because it
    identifies the model and inputs as well as the bytes.
    """
    st = src.stat()
    ident: object = [st.st_size, st.st_mtime_ns]
    try:
        with rasterio.open(src) as ds:
            tag = ds.tags().get("inference_fingerprint")
        if tag:
            ident = tag
    except rasterio.errors.RasterioIOError:
        pass
    return json.dumps([ident, year, core, halo, backend, simplify_m], sort_keys=True)


def is_current(dst: Path, fp: str) -> bool:
    "True when dst is a readable parquet stamped with exactly this fingerprint."
    if not dst.is_file():
        return False
    try:
        md = pq.ParquetFile(dst).schema_arrow.metadata or {}
    except (OSError, pa.ArrowException):
        return False
    return md.get(FP_KEY, b"").decode() == fp


def source_provenance(src: Path, year: int) -> dict:
    """The score COG's own tags, so a release can name the model it came from.

    Without this the provenance chain dies here: no published field carried
    model_sha256 or the input bands, so releases built from different checkpoints
    were byte-indistinguishable. Also cross-checks the COG's ``year`` tag against
    --year, since mixing years is silent otherwise.
    """
    try:
        with rasterio.open(src) as ds:
            tags = ds.tags()
    except rasterio.errors.RasterioIOError:
        return {}
    stamped = tags.get("year")
    if stamped and int(stamped) != year:
        raise ValueError(f"{src.name}: score COG is year {stamped}, run asked for {year}")
    keep = ("model_sha256", "inference_fingerprint", "input_bands", "normalization", "overlap")
    return {k: tags[k] for k in keep if k in tags}


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

    fp = fingerprint(Path(path), year, core, halo, backend, simplify_m)
    prov = source_provenance(Path(path), year)
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
    if not np.isclose(tr.a, 2.5) or not np.isclose(tr.e, -2.5) or tr.b != 0 or tr.d != 0:
        raise ValueError("expected north-up 2.5 m score grid")
    # Validate the grid BEFORE aux_rasters: that call fetches a DEM and three
    # land-cover years over the network, and utm_zone is None for any non-UTM CRS
    # (4326, 3857, 54009 and 5041 all measured None), so int(zone[:-1]) used to
    # raise TypeError only after all of that work had already been done.
    zone = CRS(crs).utm_zone
    if not zone:
        raise ValueError(f"{tk}: score grid CRS {crs} is not UTM (no zone); cannot own parcels")
    zn = int(zone[:-1])
    band = tk[2]
    if not band.isalpha():
        raise ValueError(f"{tk}: tile key has no MGRS band letter at position 3")
    sq_x0, sq_y0, sq_x1, sq_y1 = mgrs_square(tr, H, W)
    t = time.perf_counter()
    aux = aux_rasters(tk, year, index, crs, tr * tr.scale(4), (H // 4, W // 4))
    prof["t_aux"] = time.perf_counter() - t
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

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
            slope_mean, steep = slope_attrs(aux["slope"][a30], mean_of)
            attrs = {
                "pf_mean": mean_of(u8[0][m] / 255.0),
                "pb_mean": mean_of(u8[1][m] / 255.0),
                "frac_nodata_1q": mean_of(q16 >= 1),
                "frac_nodata_3q": mean_of(q16 >= 3),
                "frac_water": mean_of(aux["water"][a30]),
                "frac_crops_ever": mean_of(aux["crops"][a30]),
                "slope_mean": slope_mean,
                "frac_slope_gt30": steep,
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
                        "in_utm_zone": lon_in_zone(zn, band, lon),
                        "in_mgrs_square": bool(sq_x0 <= ex < sq_x1 and sq_y0 < ey <= sq_y1),
                        **{k: float(v[i]) for k, v in attrs.items()},
                    }
                )
            prof["t_poly"] += time.perf_counter() - t
            _probe(prof, "loop")
            del inst, owned, u8

    t = time.perf_counter()
    out = out_dir / f"{tk}.parquet"
    meta = {FP_KEY: fp.encode(), PROVENANCE_KEY: json.dumps(prov).encode(), b"geo": GEO_META}
    if rows:
        proj = simplify_coverage(np.array(geoms, dtype=object), simplify_m, label=tk)
        prof["t_simplify"] = time.perf_counter() - t
        t = time.perf_counter()
        g = shapely.transform(
            proj,
            lambda xy: np.column_stack(to_ll.transform(xy[:, 0], xy[:, 1])),
        )
        b = shapely.bounds(g)
        cols = {name: [r[name] for r in rows] for name in SCHEMA.names if name in rows[0]}
        for j, k in enumerate(("xmin", "ymin", "xmax", "ymax")):
            cols[k] = b[:, j]
        cols["geometry"] = shapely.to_wkb(g)
        tbl = pa.table(cols, schema=SCHEMA)
    else:
        tbl = pa.Table.from_pylist([], schema=SCHEMA)
    tbl = tbl.replace_schema_metadata(meta)
    tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
    try:
        pq.write_table(tbl, tmp, compression="zstd")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
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
    ap.add_argument("--scores", type=Path, default=Path("staging-data/raster"),
                    help="hierarchy root holding {year}/{tile}/{tile}.tif "
                         "(what inference run.py writes under "
                         "{output-dir}/raster)")
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
    ap.add_argument("--force", action="store_true", help="rerun tiles that are already current")
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_ID", 0)))
    ap.add_argument(
        "--num-shards", type=int, default=int(os.environ.get("SLURM_ARRAY_TASK_COUNT", 1))
    )
    ap.add_argument("--profile-out", type=Path, default=None)
    ap.add_argument("--verbose", action="store_true", help="print per-tile timings and metrics")
    a = ap.parse_args()

    src = a.scores / str(a.year)
    # `names or glob` would glob every tile in the year when --tile-list names an
    # EMPTY file, because an empty list is falsy -- the opposite of what the caller
    # asked for, and the "no input scores" guard below cannot fire either.
    # simplify_polygons globs only when no list was given at all; match that.
    names = a.tiles if a.tiles is not None else None
    if names is None and a.tile_list is not None:
        names = a.tile_list.read_text().split()
    # Per-item folders ({tile}/{tile}.tif): the stem==folder filter keeps a
    # sidecar such as 31UFS/31UFS.thumb.tif or a stray from being treated as
    # a score COG.
    paths = (
        sorted(p for p in src.glob("*/*.tif") if p.stem == p.parent.name)
        if names is None
        else [src / n / f"{n}.tif" for n in names]
    )
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
    # Resume on the fingerprint, not on mere existence: a regenerated score COG or a
    # changed --core/--halo/--backend/--simplify-m used to be skipped silently.
    if not a.force:
        n0 = len(paths)
        paths = [
            p
            for p in paths
            if not is_current(
                out_dir / f"{p.stem}.parquet",
                fingerprint(p, a.year, a.core, a.halo, a.backend, a.simplify_m),
            )
        ]
        if n0 != len(paths):
            print(f"{n0 - len(paths)} tiles already current, skipped", flush=True)

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

    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1, mp_context=MP_CONTEXT) as pool:
        futs = {
            pool.submit(
                process_tile, p, a.year, out_dir, index, a.core, a.halo, a.backend, a.simplify_m
            ): p.stem
            for p in paths
        }
        failures = drain(futs, report)
        by_stem = {p.stem: p for p in paths}
        failures = retry_io_failures(
            failures,
            lambda k: pool.submit(
                process_tile,
                by_stem[k],
                a.year,
                out_dir,
                index,
                a.core,
                a.halo,
                a.backend,
                a.simplify_m,
            ),
            report,
            IO_RETRY_WAIT_S,
        )
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
