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
from fbp import FTW_2E, run_windowed
from pyproj import CRS, Transformer
from rasterio.windows import Window
from scipy import ndimage as ndi

from context import aux_rasters
from ownership import SQUARES, load_squares, squares_digest, zone_keeps
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


def class_map(u8: np.ndarray, rows: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """uint8 argmax classes and float16 (background, field, boundary) probabilities of a
    window of the scores, built in row blocks so no float32 copy of the window exists."""
    _, h, w = u8.shape
    seg = np.empty((h, w), np.uint8)
    prob = np.empty((3, h, w), np.float16)
    for r in range(0, h, rows):
        pf, pb = u8[:, r : r + rows].astype(np.float32) / 255
        p = np.stack([np.clip(1 - pf - pb, 0, 1), pf, pb]).astype(np.float16)
        prob[:, r : r + rows] = p
        seg[r : r + rows] = p.argmax(0)
    return seg, prob


class ScoreWindows:
    """A score COG as ``run_windowed`` inputs, read one window at a time.

    ``seg`` (H, W) and ``prob`` (3, H, W) slice like arrays. ``run_windowed`` asks for the
    classes and then the probabilities of the same window, so both come from one read.
    """

    def __init__(self, ds) -> None:
        self.ds = ds
        self._key, self._val = None, None
        self.seg = _ScoreView(self, 0, (ds.height, ds.width))
        self.prob = _ScoreView(self, 1, (3, ds.height, ds.width))

    def window(self, rows: slice, cols: slice) -> tuple[np.ndarray, np.ndarray]:
        key = (rows.start, rows.stop, cols.start, cols.stop)
        if key != self._key:
            self._key, self._val = None, None  # free the last window before reading the next
            self._val = class_map(self.ds.read(window=Window.from_slices(rows, cols)))
            self._key = key
        return self._val


class _ScoreView:
    def __init__(self, src: ScoreWindows, which: int, shape: tuple[int, ...]) -> None:
        self.src, self.which, self.shape = src, which, shape

    def __getitem__(self, key: tuple) -> np.ndarray:
        arr = self.src.window(*key[-2:])[self.which]
        return arr[key[0]] if len(key) == 3 else arr


def parcel_attrs(inst: np.ndarray, read, aux: dict, n_id: int, rows: int = 2048):
    """Pixel count, centroid row and column, and the per-parcel means of the label image.

    ``read(r0, r1)`` returns the (2, r1 - r0, W) uint8 scores of a row strip. Sums are taken
    in row strips, so neither the scores nor the per-pixel index arrays cover the whole tile.
    """
    keys = ("pf", "pb", "nod1", "nod3", "water", "crops", "slope", "steep", "dem", "y", "x")
    tot = {k: np.zeros(n_id) for k in keys}
    cnt = np.zeros(n_id)
    for r in range(0, inst.shape[0], rows):
        lab = inst[r : r + rows]
        m = lab > 0
        if not m.any():
            continue
        u8 = read(r, r + lab.shape[0])
        idx = lab[m]
        rr, cc = np.nonzero(m)
        rr += r
        q16 = aux["nod"][np.minimum(rr // 16, aux["nod"].shape[0] - 1),
                         np.minimum(cc // 16, aux["nod"].shape[1] - 1)]
        a30 = (np.minimum(rr // 12, aux["dem"].shape[0] - 1),
               np.minimum(cc // 12, aux["dem"].shape[1] - 1))
        slope = aux["slope"][a30]
        with np.errstate(invalid="ignore"):
            steep = (slope > 30).astype(np.float64)
        vals = {
            "pf": u8[0][m] / 255.0, "pb": u8[1][m] / 255.0,
            "nod1": q16 >= 1, "nod3": q16 >= 3, "water": aux["water"][a30],
            "crops": aux["crops"][a30], "slope": slope, "steep": steep, "dem": aux["dem"][a30],
            "y": rr, "x": cc,
        }
        cnt += np.bincount(idx, minlength=n_id)
        for k, v in vals.items():
            tot[k] += np.bincount(idx, v, n_id)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = {k: v / cnt for k, v in tot.items()}
    attrs = {
        "pf_mean": mean["pf"],
        "pb_mean": mean["pb"],
        "frac_nodata_1q": mean["nod1"],
        "frac_nodata_3q": mean["nod3"],
        "frac_water": mean["water"],
        "frac_crops_ever": mean["crops"],
        # Where the DEM has no coverage slope is unknown, not flat: bincount propagates NaN
        # into slope_mean, and the steep share is masked to match rather than counting an
        # unknown as not steep (nan_to_num once made coastal parcels pass as 0 degrees)
        "slope_mean": mean["slope"],
        "frac_slope_gt30": np.where(np.isnan(mean["slope"]), np.nan, mean["steep"]),
        "elev_mean": mean["dem"],
    }
    return cnt, mean["y"], mean["x"], attrs


#: Method id of the published 2017-2025 vectors, recorded in every outline stamp and fingerprint.
#: It names the BoundaryVote setting fbp ships as ``fbp.FTW_2E``: foreground ``nbg``, seeds from
#: the boundary surface at depth 0.01, a watershed line kept when 50% of its pixels have
#: p(boundary) >= p(field) (``t0.5``), regions with mean p(field) under 25% dropped (``R25``),
#: parcels of the p(field) > 10% mask that the result covers less than half re-adopted
#: (``F10``), skipped when under 2% of that mask is uncovered (``G2``), and parcels under 900 m2
#: dropped (``A900``). Changing the string changes every fingerprint, so a resumed run would
#: redo every tile.
SPEC = "nbg-pb-h0.01-t0.5+R25+F10+G2+A900"


#: What every outline stamp records. ``fbp.FTW_2E`` runs with ``fast=True``, which the published
#: run recorded as the ``+q1`` suffix and the ``fast`` backend.
STAMP = {"spec": SPEC + "+q1", "backend": "fast"}

#: How a tile is cut into windows. It is in the fingerprint, so outlines from the old per-window
#: loop, which left a field cut by two windows as two parcels, are not mixed with these.
WINDOWING = "fbp.run_windowed"


#: Wait before the one retry pass over tiles that failed on a remote read.
IO_RETRY_WAIT_S = 60
REPORT_EVERY = 50


#: Side of an MGRS grid square, metres.
MGRS_SQUARE_M = 100_000.0
#: A tile's square must sit inside its raster; allow this much slack (the published rasters
#: overhang the square by 0-80 m, and a padded raster by its pad).
MGRS_FIT_TOLERANCE_M = 250.0
#: Version of the parcel-ownership rules (``in_utm_zone``, ``in_mgrs_square``, ``claimed``).
#: It is part of the outline fingerprint, so a changed rule rewrites every tile on the next run
#: instead of resuming onto parquet written under the old one -- otherwise a release silently
#: mixes vintages and the parquet does not say which. Bump it with every rule change.
#: 1: ``origin + 5 km`` / ``extent - 5 km`` (a 90.08 km square). 2: the raster's own 100 km cell.
#: 3: orphan-strip claims (``ownership.py``); the owned-squares file is in the fingerprint too.
OWNERSHIP_RULES = 3


def mgrs_square(tr, height: int, width: int) -> tuple[float, float, float, float]:
    """The 100 km UTM/MGRS cell the raster's north-west corner snaps to, as (x0, y0, x1, y1).

    The published score rasters are 100.08 km (40,032 px x 2.5 m) with their origin 0-80 m
    off the square, so the square is the 100 km grid cell that the raster's north-west corner
    snaps to: ``round(origin / 100 km)``. Rounding to the *nearest* cell is stable for any
    offset or padding under 50 km, which a floor/ceil of ``origin +/- pad`` is not (a sub-pixel
    offset flips floor/ceil by a whole 100 km at the exact boundary the origin sits on).

    It is the cell the raster's *pixels* cover, which for a stacked ``_r_c`` sub-tile is not
    the cell its key names: 59GQQ_0_1 begins 100,080 m below 59GQQ_0_0 and so owns the 59GQP
    cell (27 published items are of that shape). The square follows the pixels, not the key;
    a mismatch between the two is correct and must not be "fixed" back to the key.

    Two stacked sub-tiles therefore claim abutting squares whose shared edge sits 80 m north
    of where their rasters abut, so an 80 m x 100 km strip (~8 km2) has pixels only in the
    upper tile while belonging to the lower tile's square, and is owned by neither. Only
    59GQQ has both siblings present, so that costs 9 items -- one per year.

    Neighbouring squares abut exactly, which makes ``fiboa_convert``'s cross-tile seam union
    load-bearing for parcels on the boundary: such a parcel is truncated at each raster's own
    data edge -- not flagged, since ``touches_window_edge`` deliberately excludes the raster
    boundary -- and each neighbour's piece is centred inside its own square, so both publish a
    partial copy unless the union rejoins them. Same-zone rasters overlap by only 40-120 m and
    ``MIN_OVERLAP`` wants 10% of the smaller piece, so a field reaching more than ~1.2 km each
    side of a same-zone seam stays two overlapping halves (recorded in test_fiboa_convert).

    An earlier version took ``origin + 5 km`` and ``extent - 5 km`` as the square, which
    assumes a 110 km raster with a 5 km pad. On the real 100.08 km rasters that claims a
    90.08 km square (81% of the true area): the outer ~5 km frame of every tile is owned by
    nobody and silently dropped. The square is checked against the raster extent so a
    raster that cannot contain it fails loudly instead of owning a wrong square.

    Recovering that frame is not pure gain: it also admits the inference edge artifact
    pipeline/README.md measures -- spurious high field probability in the outermost rows of
    some score COGs, e.g. ~640 m of DN 109 over open North Sea on 31UET_0_0 -- which the
    5 km inset had been masking by accident. Nothing downstream filters it, so the vector
    product now carries those parcels.
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
        pa.field("claimed", pa.bool_()),
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


#: The score-COG layouts inference writes under ``{--scores}/{year}/``: the per-item
#: folder (``--layout item``) and the bucket's grouped key (``--layout hive``). Both end
#: in ``{tile}/{tile}.tif``; only the depth above it differs, so discovery accepts either
#: and a half-migrated tree needs no flag here.
SCORE_GLOBS = ("*/*.tif", "zone=*/gzd=*/*/*.tif")


def score_paths(src: Path, names: list[str] | None) -> list[Path]:
    """The score COGs under one year directory, in either inference layout.

    The stem==folder filter keeps a sidecar such as 31UFS/31UFS.thumb.tif or a
    stray from being treated as a score COG. A named tile resolves to whichever
    layout holds it, preferring the grouped key, and otherwise keeps the per-item
    path so the missing-input message names the default layout.
    """
    if names is None:
        return sorted(
            {p for g in SCORE_GLOBS for p in src.glob(g) if p.stem == p.parent.name}
        )
    found = []
    for n in names:
        grouped = src / f"zone={n[:2]}" / f"gzd={n[:3]}" / n / f"{n}.tif"
        found.append(grouped if grouped.exists() else src / n / f"{n}.tif")
    return found


def fingerprint(
    src: Path, year: int, core: int, halo: int, simplify_m: float, squares: Path = SQUARES
) -> str:
    """Identity of the source COG plus every flag that changes the output.

    Resume used to test only that the output file exists, so a regenerated score
    COG or a changed --core/--halo/--simplify-m silently published the
    previous run's parcels. Built like the siblings: PR2's run.py
    inference_fingerprint (size, mtime, model hash, flags) and
    simplify_polygons.simplify_fingerprint (tol, size, mtime). The COG's own
    inference_fingerprint tag is preferred when PR2 stamped one, because it
    identifies the model and inputs as well as the bytes.

    OWNERSHIP_RULES is in here too, because which parcels a tile claims is as
    result-affecting as any flag: without it a resumed run after an ownership
    change keeps the previous rule's parquet, and the release silently mixes
    vintages that the parquet cannot be asked to tell apart. The same goes for
    ``SPEC``, the BoundaryVote method id, and the owned squares, which decide the claims.
    The ``"fast"`` entry is the backend, which used to be
    a flag; it stays so outlines written before the flag was dropped still resume.
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
    return json.dumps(
        [ident, year, core, halo, "fast", simplify_m, OWNERSHIP_RULES, SPEC,
         squares_digest(squares), WINDOWING],
        sort_keys=True,
    )


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


def outline_provenance(src: Path, year: int) -> dict:
    """The stamp written into every outline file: the COG's tags plus the method actually run.

    merge_polygons reads the spec into ``_summary.json`` and fiboa_convert writes it into
    ``determination:details``, so a release states the spec and backend that
    produced it instead of a hardcoded one.
    """
    return {**source_provenance(src, year), **STAMP}


def process_tile(
    path: Path,
    year: int,
    out_dir: Path,
    index: Path,
    core: int,
    halo: int,
    simplify_m: float = 0.0,
    squares: Path = SQUARES,
) -> dict:
    "Stage the tile's COG locally if the scratch copy is gone, run it, clean up."
    if not path.is_file():
        raise FileNotFoundError(path)
    prof = _run_tile(str(path), path.stem, year, out_dir, index, core, halo, simplify_m, squares)
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
    simplify_m: float,
    squares: Path = SQUARES,
) -> dict:
    from polygons import simplify_coverage

    fp = fingerprint(Path(path), year, core, halo, simplify_m, squares)
    owned_squares = load_squares(squares)
    prov = outline_provenance(Path(path), year)
    prof = {
        "tile_key": tk,
        **STAMP,
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
    prof["windows"] = len(list(_windows(H, core, halo))) * len(list(_windows(W, core, halo)))
    with rasterio.open(path) as ds:
        t = time.perf_counter()
        _probe(prof, "bv")
        # fbp reads one window at a time, keeps each parcel in the window whose core holds
        # its centroid, and merges the copies of a field cut by two windows into one parcel
        scores = ScoreWindows(ds)
        inst = run_windowed(FTW_2E, scores.seg, scores.prob, pixel_area_m2=PX_M2, core=core,
                            halo=halo)
        del scores
        prof["t_bv"] += time.perf_counter() - t
        _probe(prof, "attr")
        t = time.perf_counter()
        n_id = int(inst.max()) + 1
        cnt, cy, cx, attrs = parcel_attrs(
            inst, lambda r0, r1: ds.read(window=Window(0, r0, W, r1 - r0)), aux, n_id
        )
        sl = ndi.find_objects(inst)
        prof["t_attr"] += time.perf_counter() - t
        _probe(prof, "poly")
        t = time.perf_counter()
        for r0, r1, R0, R1 in _windows(H, core, halo):
            for c0, c1, C0, C1 in _windows(W, core, halo):
                ids = np.nonzero((cnt > 0) & (cy >= r0) & (cy < r1) & (cx >= c0) & (cx < c1))[0]
                ids = ids[ids > 0]
                if not len(ids):
                    continue
                # a merged parcel can reach past this window, so polygonize over all its pixels
                y0 = min(sl[i - 1][0].start for i in ids)
                y1 = max(sl[i - 1][0].stop for i in ids)
                x0 = min(sl[i - 1][1].start for i in ids)
                x1 = max(sl[i - 1][1].stop for i in ids)
                lut = np.zeros(n_id, np.int32)
                lut[ids] = ids
                owned = lut[inst[y0:y1, x0:x1]]
                wtr = tr * rasterio.Affine.translation(x0, y0)
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
                    ex, ey = tr * (cx[i] + 0.5, cy[i] + 0.5)
                    lon, lat = to_ll.transform(ex, ey)
                    in_square = bool(sq_x0 <= ex < sq_x1 and sq_y0 < ey <= sq_y1)
                    in_zone, claimed = zone_keeps(
                        to_ll, zn, band, (lon, lat, ey), (tr * (s[1].start, 0))[0],
                        (tr * (s[1].stop, 0))[0], in_square, owned_squares,
                    )
                    # past the halo edge of the window that owns it (not the raster edge)
                    touch = ((R0 > 0 and s[0].start <= R0) or (R1 < H and s[0].stop >= R1)
                             or (C0 > 0 and s[1].start <= C0) or (C1 < W and s[1].stop >= C1))
                    rows.append(
                        {
                            "tile_key": tk,
                            "year": year,
                            "parcel_id": len(rows) + 1,
                            "area_m2": float(cnt[i]) * PX_M2,
                            "n_parts": len(ps),
                            "ext_h_px": s[0].stop - s[0].start,
                            "ext_w_px": s[1].stop - s[1].start,
                            "touches_window_edge": bool(touch),
                            "in_utm_zone": in_zone,
                            "in_mgrs_square": in_square,
                            "claimed": claimed,
                            **{k: float(v[i]) for k, v in attrs.items()},
                        }
                    )
        prof["t_poly"] += time.perf_counter() - t
        _probe(prof, "loop")
        del inst

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
                    help="hierarchy root holding {year}/{tile}/{tile}.tif or the "
                         "grouped {year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif "
                         "(what inference run.py writes under "
                         "{output-dir}/raster, either --layout)")
    ap.add_argument("--out-root", type=Path, default=Path("outlines"))
    ap.add_argument("--index-dir", type=Path, default=Path("index"))
    ap.add_argument("--tiles", nargs="*", default=None)
    ap.add_argument("--tile-list", type=Path, default=None)
    ap.add_argument("--core", type=int, default=8192)
    ap.add_argument("--halo", type=int, default=512)
    ap.add_argument(
        "--simplify-m",
        type=float,
        default=0.0,
        help="coverage-simplify tolerance, metres (default off; use simplify_polygons.py)",
    )
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument(
        "--owned-squares", type=Path, default=SQUARES,
        help="squares of the run's keep-list tiles (ownership.py squares); r1 used "
        "owned_squares_2e.csv",
    )
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
    paths = score_paths(src, names)
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
    # changed --core/--halo/--simplify-m used to be skipped silently.
    if not a.force:
        n0 = len(paths)
        paths = [
            p
            for p in paths
            if not is_current(
                out_dir / f"{p.stem}.parquet",
                fingerprint(p, a.year, a.core, a.halo, a.simplify_m, a.owned_squares),
            )
        ]
        if n0 != len(paths):
            print(f"{n0 - len(paths)} tiles already current, skipped", flush=True)

    index = sorted(a.index_dir.glob(f"tile_index_{a.year}*.parquet"))
    if paths and not index:
        ap.error("no quarterly source index")
    print(
        f"{len(paths)} tiles, core={a.core} halo={a.halo}, "
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
                process_tile, p, a.year, out_dir, index, a.core, a.halo, a.simplify_m,
                a.owned_squares,
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
                a.simplify_m,
                a.owned_squares,
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
