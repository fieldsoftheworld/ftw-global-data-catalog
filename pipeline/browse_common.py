#!/usr/bin/env python3
"""Shared core for the two browse products: the per-year global overview
COG (``make_overview.py``) and the per-item thumbnails
(``make_item_thumbnails.py``).

Everything in here is deliberately small and shared because the two jobs
have to agree on three things, and a disagreement is invisible until
someone looks at a map:

* the **colormap**, so an item thumbnail and the global overview say the
  same thing about the same pixel;
* the **transparency floor**, for the same reason;
* which **internal overview** of a source COG gets opened, because that
  is the difference between a browse layer costing gigabytes of reads and
  costing terabytes.

The GDAL environment block is carried verbatim from
``s2-mosaics-catalog/tools/make_overview.py``, where every entry in it
was put there by a failure on a real run. It is not a style choice.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The public index of every beta raster item. `href` is the authoritative
# object URL; nothing here ever builds a key from a tile id.
INDEX_URL = ("https://data.source.coop/ftw/global-data-beta/"
             "index/raster.parquet")

# Source COG geometry, verified with gdalinfo over https on 2026-10-01
# against 2017/01KFS_0_0, 2017/32UPU_0_0, 2020/32UPU_0_0, 2025/01KFS_0_0
# and 2019/36MXE_0_0 -- identical on all five:
#
#     Size is 40032, 40032      Pixel Size = (2.5, -2.5)
#     Band 1 "field"    Byte, Scale 1/255, Block 512x512
#     Band 2 "boundary" Byte, Scale 1/255
#     Overviews: 10008, 5004, 2502, 1251, 626
#     (no NoData, no mask band, COMPRESS=ZSTD, LAYOUT=COG)
#
# 40032/10008 is 4, so the overview factors are 4..64 and there are FIVE
# levels, indices 0..4. There is NO 2x overview. This matters: a
# factor list that began at 2 would make `overview_level()` return 5 for
# zoom 10, and OVERVIEW_LEVEL=5 does not exist on these files.
GSD = 2.5
FACTORS = (4, 8, 16, 32, 64)
FULL_SHAPE = 40032
# The coarsest level, 626 x 626 at 160 m/px. The per-item thumbnails
# always read this one and nothing else.
COARSEST_LEVEL = len(FACTORS) - 1

# Web Mercator, at zoom 0, in metres per pixel of a 256 px tile.
Z0_RESOLUTION = 2 * 20_037_508.342789244 / 256

# The browse layer's transparency floor, as a probability. Measured
# ruling -- see `make_overview.py`'s docstring. 0.10 of 255 is DN 26.
MIN_PROB = 0.10

# The FTW inference app's dark background, which per-item thumbnails are
# composited over (docs/plan.md Phase 4.2). The overview does not use it:
# it carries a real alpha band instead.
BG_RGB = (11, 20, 20)  # #0b1414

GDAL_ENV = {
    # The bucket has no directory listing worth paying for, and a stray
    # sidecar probe per tile would be 67,000 wasted round trips.
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "268435456",
    "GDAL_CACHEMAX": "1024",
    # A year is tens of thousands of range reads against one host, and
    # some of them will be cut off in the middle -- measured on the s2
    # catalog as `TIFFFillTile: got 108718 bytes, expected 148406`.
    # Without a retry that is a failed job hours in.
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    # And a transfer that stalls rather than fails would hang the job
    # forever: below 1 kB/s for 30 s, give up and let the retry handle it.
    "GDAL_HTTP_LOW_SPEED_LIMIT": "1000",
    "GDAL_HTTP_LOW_SPEED_TIME": "30",
}


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def gdal_env() -> dict[str, str]:
    return {**os.environ, **GDAL_ENV}


def run(cmd: list[str], what: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, env=gdal_env())
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:], file=sys.stderr)
        sys.exit(f"{what} failed: {' '.join(cmd[:3])} ...")


def require_gdal(tools: tuple[str, ...] = (
        "gdalbuildvrt", "gdalwarp", "gdal_translate", "gdaldem",
        "gdalinfo")) -> dict[str, bool]:
    """Fail early if the toolchain is not there; report what it can do."""
    for tool in tools:
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is not on PATH; this step needs GDAL "
                     "(the rails `ftw` env has 3.12.3: "
                     "/u/cholmes/micromamba/envs/ftw/bin)")
    formats = subprocess.run(["gdalinfo", "--formats"], capture_output=True,
                             text=True).stdout
    return {"webp": "WEBP" in formats, "png": "PNG" in formats}


def resolution(zoom: int) -> float:
    return Z0_RESOLUTION / (2 ** zoom)


def overview_level(target: float, oversample: float) -> int:
    """Which internal overview of a source tile to open.

    Take the coarsest level that is no more than ``oversample`` times
    coarser than the target resolution, so the read is as small as it can
    be without the output visibly softening. Returns a 0-based index into
    `FACTORS`, which is what GDAL's ``OVERVIEW_LEVEL`` open option wants;
    -1 would mean full resolution.

    At the default zoom 10 (152.87 m/px) this is level 4 -- the 64x
    overview at 160 m, within 1.047x of the target -- so a 40032 x 40032
    tile is read as 626 x 626. That is 1/4096th of the pixels.
    """
    best = -1
    for index, factor in enumerate(FACTORS):
        if GSD * factor <= target * oversample:
            best = index
    return best


# ---------------------------------------------------------------- colors

def load_bins(path: Path | None = None) -> tuple[list[str], list[int]]:
    """The approved `field-prob` step expression from style_bins.json.

    One file is the source of truth for the vector styles and for both
    browse rasters, so the raster browse layer cannot drift away from the
    legend the browser draws next to it.
    """
    path = path or HERE / "style_bins.json"
    bins = json.loads(path.read_text())["field-prob"]
    colors, edges = bins["colors"], bins["edges"]
    if len(colors) != len(edges) + 1:
        sys.exit(f"{path}: field-prob has {len(colors)} colors and "
                 f"{len(edges)} edges; a step expression needs n+1 colors")
    if sorted(edges) != edges or len(set(edges)) != len(edges):
        sys.exit(f"{path}: field-prob edges are not strictly increasing")
    return colors, edges


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def ramp(min_prob: float = MIN_PROB,
         bins_path: Path | None = None) -> tuple[int, list[tuple]]:
    """The fixed probability-to-colour ramp, as (floor_dn, anchors).

    `anchors` is a list of (dn, r, g, b) that gdaldem interpolates
    between. The placement is mechanical, so there is nothing to choose
    per year: the step expression's `colors[0]` -- the colour the vector
    styles paint below the first edge -- sits at the transparency floor,
    and `colors[i+1]` sits exactly on `edges[i]`, the DN for that score.
    The top colour is held flat to 255.

    A continuous ramp rather than a hard 5-class colormap: the raster is
    a probability *surface*, and JPEG, which the overview COG is
    compressed with, rings audibly at every hard colour edge. The ramp
    crosses each approved colour exactly at its approved score, so a
    gradient legend with stops at those scores is exactly right.
    """
    colors, edges = load_bins(bins_path)
    floor_dn = math.ceil(min_prob * 255)
    if not 1 <= floor_dn < round(edges[0] * 2.55):
        sys.exit(f"--min-prob {min_prob} puts the transparency floor at DN "
                 f"{floor_dn}, which is not between 1 and the first score "
                 f"edge ({edges[0]})")
    anchors = [(floor_dn, *_rgb(colors[0]))]
    for edge, color in zip(edges, colors[1:]):
        anchors.append((round(edge * 2.55), *_rgb(color)))
    anchors.append((255, *_rgb(colors[-1])))
    # The overview chain uses pure black as its nodata sentinel through
    # the whole VRT chain, so no colour the ramp can produce may be black
    # or anywhere near it. Every RdYlGn entry is far from it, but assert
    # rather than assume: a future palette edit must fail here loudly
    # instead of punching transparent holes in the mosaic.
    for dn, r, g, b in anchors:
        if r + g + b < 24:
            sys.exit(f"ramp anchor at DN {dn} is ({r},{g},{b}), too close "
                     "to the (0,0,0) nodata sentinel the overview uses")
    return floor_dn, anchors


def write_color_table(dest: Path, below_rgb: tuple[int, int, int],
                      min_prob: float = MIN_PROB,
                      bins_path: Path | None = None) -> Path:
    """A gdaldem color-relief table for the ramp, written to `dest`.

    `below_rgb` is what everything under the transparency floor becomes:
    (0, 0, 0) for the overview, where black is the nodata sentinel the
    whole VRT chain keys off, and the app background for the per-item
    thumbnails, which are composited rather than masked.

    DN is integral, so the one-DN step from `floor_dn - 1` to `floor_dn`
    leaves gdaldem nothing to interpolate across: the floor is a clean
    edge, not a gradient into the background.
    """
    floor_dn, anchors = ramp(min_prob, bins_path)
    lines = [f"0 {below_rgb[0]} {below_rgb[1]} {below_rgb[2]}",
             f"{floor_dn - 1} {below_rgb[0]} {below_rgb[1]} {below_rgb[2]}"]
    lines += [f"{dn} {r} {g} {b}" for dn, r, g, b in anchors]
    dest.write_text("\n".join(lines) + "\n")
    return dest


# ----------------------------------------------------------- tile listing

def _connect(index: str):
    import duckdb
    con = duckdb.connect()
    if index.startswith(("http://", "https://")):
        con.execute("INSTALL httpfs; LOAD httpfs; SET http_retries=20")
    return con


# Only ever read these; `geometry` is a large column this pipeline has no
# use for, and excluding it keeps a cached copy of the index small.
INDEX_COLUMNS = ("year", "tile_key", "epsg", "href",
                 "xmin", "ymin", "xmax", "ymax")


def cache_index(index: str, cache: Path) -> Path:
    """A local copy of the index parquet, fetched once.

    An sbatch array of 75 tasks per year would otherwise read the remote
    parquet 675 times across the campaign. The write is atomic and the
    temp name carries the pid, so tasks that start together race
    harmlessly: the loser's copy is simply thrown away.
    """
    if cache.is_file():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
    con = _connect(index)
    cols = ", ".join(INDEX_COLUMNS)
    con.execute(f"COPY (SELECT {cols} FROM '{index}') "
                f"TO '{tmp}' (FORMAT parquet)")
    os.replace(tmp, cache)
    say(f"{cache}: index cached, {cache.stat().st_size / 1e6:,.1f} MB")
    return cache


def tiles_of(index: str, year: int,
             bbox: tuple[float, float, float, float] | None = None,
             only: list[str] | None = None) -> list[tuple[str, int, str]]:
    """The year's (tile_key, epsg, href) rows, from the published index.

    The index is authoritative for the object URL, so no key is ever
    constructed here -- which also means this keeps working when the
    published raster layout moves (it is moving to per-item folders,
    `raster/{year}/{tile}/{tile}.tif`, and the index is being rewritten
    with it).

    `--bbox` keeps only the tiles whose footprint meets it and `--tiles`
    names them outright, which is how a smoke test builds two UTM zones
    of the Netherlands instead of the world.
    """
    where = [f"year = {int(year)}"]
    if bbox:
        w, s, e, n = bbox
        where.append(f"xmin < {e} AND xmax > {w} "
                     f"AND ymin < {n} AND ymax > {s}")
    if only:
        names = ", ".join("'" + t.replace("'", "") + "'" for t in only)
        where.append(f"tile_key IN ({names})")
    sql = (f"SELECT tile_key, epsg, href FROM '{index}' "
           f"WHERE {' AND '.join(where)} ORDER BY tile_key")
    rows = _connect(index).execute(sql).fetchall()
    return [(str(t), int(e), str(h)) for t, e, h in rows]


def vsicurl(href: str) -> str:
    """The index stores public https URLs; GDAL wants them via /vsicurl.

    Never s3:// on a compute node -- the EC2 metadata probe is
    blackholed there and the call hangs (pipeline/README.md).
    """
    if href.startswith("s3://"):
        sys.exit(f"{href}: the index's s3_href column must not be used on "
                 "rails; s3:// hangs on compute nodes. Use `href`.")
    return href if href.startswith("/vsi") else f"/vsicurl/{href}"
