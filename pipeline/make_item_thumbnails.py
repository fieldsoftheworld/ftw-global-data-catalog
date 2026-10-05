#!/usr/bin/env python3
"""One small PNG per beta raster item, from that COG's coarsest overview.

    python3 pipeline/make_item_thumbnails.py --year 2025 \\
        --out /u/cholmes/ftw-browse/publish --work /u/cholmes/ftw-browse/work
    python3 pipeline/make_item_thumbnails.py --year 2025 --out p \\
        --tiles 31UFU_0_0,32ULD_0_0            # a couple of real tiles
    python3 pipeline/make_item_thumbnails.py --year 2025 --out p \\
        --chunk 100 --task 7                   # one array task's slice

Writes ``{out}/raster/{year}/{tile}/{tile}.thumb.png`` -- the published
per-item folder layout, so the thumbnail sits beside its COG and its item
JSON. Another agent registers it as the item's ``thumbnail`` asset.

Resumable, and that is the whole operating model: 67,197 renders across
nine years, so a task that is interrupted, or a year that is rerun
because a hundred tiles failed, must not redo the ones that are already
on disk. An existing non-empty PNG is skipped unless ``--force``.

What each render reads
----------------------
Exactly one thing: the tile's coarsest internal overview, 626 x 626 at
160 m/px, opened with ``-oo OVERVIEW_LEVEL=4``. **Never** full
resolution -- a 40032 x 40032 read would be ~450 MB per tile and 30 TB
across the campaign, against 393 kB per tile and 26 GB this way. The
level is an argument but it defaults to the coarsest and the job has no
reason to change it.

Reading at the overview and resizing locally, rather than letting GDAL
pick its own overview for an ``-outsize``, is deliberate: it makes the
read volume a property of this script rather than of GDAL's heuristics,
so the number above is the number.

The three steps, per tile
-------------------------
1. ``gdal_translate -b 1 -oo OVERVIEW_LEVEL=4 -outsize W 0 -r average``
   into a small local GeoTIFF. The average happens here, on the
   probability band, which is the only place it is meaningful: averaging
   after the colormap would blend RdYlGn colours and would bleed the
   background colour across the edge of the data.
2. ``gdaldem color-relief -of VRT`` with the same ramp the global
   overview uses, read from ``pipeline/style_bins.json`` through
   ``browse_common.ramp``, so an item thumbnail and the overview say the
   same thing about the same pixel. Everything below the transparency
   floor (p < 0.10, DN < 26) becomes ``#0b1414``, the FTW inference
   app's dark background, per docs/plan.md Phase 4.2 -- the item
   thumbnails are composited rather than masked, so that a tile with 5%
   field coverage reads as data on a dark card instead of as a mostly
   blank image.
3. ``gdal_translate -of PNG``.

The local GeoTIFF goes in ``$TMPDIR``, which on rails must be on ``/u``
and never ``/tmp`` (a compute node's ``/tmp`` is tmpfs and counts
against the job's cgroup).

Array chunking
--------------
``--chunk`` and ``--task`` take a deterministic slice of the year's
tiles, ordered by ``tile_key``, so an sbatch array covers the year with
no coordination between tasks and ``--array=<n>`` resubmits exactly the
slice that failed. ``--chunk`` is the only thing that sets the mapping,
so it must be the same on a resubmit as it was on the original -- the
job script holds it in one place for that reason.

Within a task the renders run on a small thread pool: each one is
latency-bound on a single ~400 kB range read, not CPU-bound, so eight
workers turn a 100-tile task from about three minutes into about half a
minute without using more than one core's worth of CPU.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browse_common as bc  # noqa: E402

ATTEMPTS = 3


def render(job: tuple[str, str, Path, Path, int, int, Path]) -> tuple[str, float, str]:
    """One tile's thumbnail. Returns (tile, seconds, error-or-empty).

    Retried like the overview's warps, and for the same measured reason:
    the failure that happens in practice is a truncated range read, which
    comes back as a 206 so GDAL's own retry never sees it. Only redoing
    the read recovers.

    A tile that fails every attempt is returned rather than raised. One
    bad tile must not cost the other ninety-nine in the task, and the
    caller reports the list so a rerun picks up exactly those (a rerun
    skips the PNGs that landed, so it is cheap).
    """
    tile, href, dest, table, width, level, tmpdir = job
    if dest.is_file() and dest.stat().st_size > 0:
        return tile, 0.0, ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        with tempfile.TemporaryDirectory(dir=tmpdir) as td:
            small = Path(td) / "p.tif"
            rgb = Path(td) / "rgb.vrt"
            png = Path(td) / "t.png"
            steps = (
                (["gdal_translate", "-q", "-b", "1",
                  "-oo", f"OVERVIEW_LEVEL={level}",
                  "-outsize", str(width), "0", "-r", "average",
                  "-of", "GTiff", "-co", "COMPRESS=ZSTD",
                  bc.vsicurl(href), str(small)], "read"),
                (["gdaldem", "color-relief", "-q", "-of", "VRT",
                  str(small), str(table), str(rgb)], "colorize"),
                (["gdal_translate", "-q", "-of", "PNG",
                  "-co", "ZLEVEL=9", str(rgb), str(png)], "png"),
            )
            failed = ""
            for cmd, what in steps:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   env=bc.gdal_env())
                if r.returncode != 0 or not Path(cmd[-1]).is_file():
                    failed = f"{what}: {((r.stderr or r.stdout)[-200:]).strip()}"
                    break
            if not failed:
                os.replace(png, dest)
                # The PNG driver drops a world file and a .aux.xml beside
                # its output; neither belongs in the published tree.
                for stray in (dest.with_suffix(".png.aux.xml"),
                              dest.with_suffix(".wld"),
                              dest.with_suffix(".png.wld")):
                    stray.unlink(missing_ok=True)
                return tile, time.monotonic() - t0, ""
            last = failed
        if attempt < ATTEMPTS:
            time.sleep(3 * attempt)
    return tile, time.monotonic() - t0, last or "render failed"


def build(year: int, index: str, out: Path, table: Path, width: int,
          level: int, workers: int, chunk: int, task: int | None,
          only: list[str] | None, bbox, force: bool,
          tmpdir: Path) -> int:
    tiles = bc.tiles_of(index, year, bbox, only)
    if not tiles:
        sys.exit(f"no tiles match for {year}; nothing to render")
    total = len(tiles)
    if task is not None:
        tiles = tiles[task * chunk:(task + 1) * chunk]
        if not tiles:
            bc.say(f"{year}: task {task} is past the end of {total:,} "
                   f"tile(s) at --chunk {chunk}; nothing to do")
            return 0
        bc.say(f"{year}: task {task} covers tiles "
               f"{task * chunk:,}..{task * chunk + len(tiles) - 1:,} "
               f"of {total:,} ({tiles[0][0]}..{tiles[-1][0]})")
    else:
        bc.say(f"{year}: {total:,} tile(s)")

    jobs = []
    for tile, _epsg, href in tiles:
        dest = out / "raster" / str(year) / tile / f"{tile}.thumb.png"
        if force:
            dest.unlink(missing_ok=True)
        jobs.append((tile, href, dest, table, width, level, tmpdir))

    t0 = time.monotonic()
    done, skipped, broken, nbytes = 0, 0, [], 0
    with cf.ThreadPoolExecutor(workers) as pool:
        for n, (tile, secs, err) in enumerate(pool.map(render, jobs), 1):
            if err:
                broken.append((tile, err))
                print(f"  {tile}: FAILED after {ATTEMPTS} attempts: {err}",
                      file=sys.stderr)
            elif secs == 0.0:
                skipped += 1
            else:
                done += 1
            if n % 25 == 0:
                bc.say(f"  {n}/{len(jobs)}, {done} rendered, {skipped} "
                       f"skipped, {len(broken)} failed, "
                       f"{time.monotonic() - t0:,.0f}s")
    for tile, _epsg, _href in tiles:
        p = out / "raster" / str(year) / tile / f"{tile}.thumb.png"
        if p.is_file():
            nbytes += p.stat().st_size
    bc.say(f"{year}: {done} rendered, {skipped} already there, "
           f"{len(broken)} failed, {time.monotonic() - t0:,.1f}s, "
           f"{nbytes / 1e6:,.1f} MB on disk for {len(tiles)} tile(s)")
    if broken:
        print(f"{len(broken)} tile(s) did not render: "
              f"{', '.join(t for t, _ in broken)}. Rerun this task -- the "
              "PNGs that landed are skipped, so it retries only these.",
              file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True, help="the publish tree; each "
                    "thumbnail lands at "
                    "{out}/raster/{year}/{tile}/{tile}.thumb.png")
    ap.add_argument("--index", default=bc.INDEX_URL,
                    help="raster.parquet, a URL or a local path; its `href` "
                         "column is the only source of object URLs")
    ap.add_argument("--index-cache", help="keep a local copy of the index "
                    "here, so an array of tasks fetches it once")
    ap.add_argument("--width", type=int, default=512,
                    help="thumbnail width in px (default 512; the source "
                         "overview is 626, so this is a mild downsample)")
    ap.add_argument("--overview-level", type=int, default=bc.COARSEST_LEVEL,
                    help="which internal overview to open "
                         f"(default {bc.COARSEST_LEVEL}, the coarsest: "
                         "626 x 626 at 160 m/px). Raising it reads more.")
    ap.add_argument("--min-prob", type=float, default=bc.MIN_PROB,
                    help="field probability below which a pixel becomes "
                         f"background (default {bc.MIN_PROB}; the same "
                         "ruling the global overview uses)")
    ap.add_argument("--workers", type=int, default=8,
                    help="renders in flight at once (default 8); these are "
                         "latency-bound range reads, not computation")
    ap.add_argument("--chunk", type=int, default=100,
                    help="tiles per array task (default 100)")
    ap.add_argument("--task", type=int,
                    help="which chunk to render, 0-based "
                         "($SLURM_ARRAY_TASK_ID); omit for the whole year")
    ap.add_argument("--tiles", help="comma-separated tile_keys, instead of "
                                    "the whole year")
    ap.add_argument("--bbox", help="west,south,east,north in degrees")
    ap.add_argument("--force", action="store_true",
                    help="re-render thumbnails that already exist")
    a = ap.parse_args(argv)

    bc.require_gdal(("gdal_translate", "gdaldem", "gdalinfo"))
    if a.overview_level < 0:
        sys.exit("--overview-level must be >= 0; this job never reads a "
                 "source COG at full resolution")
    out = Path(a.out)
    tmpdir = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    tmpdir.mkdir(parents=True, exist_ok=True)

    index = a.index
    if a.index_cache:
        index = str(bc.cache_index(index, Path(a.index_cache)))

    # Composited over the app background, not masked: these are cards.
    table = bc.write_color_table(tmpdir / f"thumb-colors-{os.getpid()}.txt",
                                 bc.BG_RGB, a.min_prob)
    return build(a.year, index, out, table, a.width, a.overview_level,
                 a.workers, a.chunk, a.task,
                 [t.strip() for t in a.tiles.split(",")] if a.tiles else None,
                 tuple(float(v) for v in a.bbox.split(",")) if a.bbox else None,
                 a.force, tmpdir)


if __name__ == "__main__":
    raise SystemExit(main())
