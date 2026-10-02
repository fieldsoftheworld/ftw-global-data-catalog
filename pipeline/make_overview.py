#!/usr/bin/env python3
"""Build one year's browse layer: a global Web-Mercator overview COG.

    python3 pipeline/make_overview.py --year 2025 \\
        --work /u/cholmes/ftw-browse/work --out /u/cholmes/ftw-browse/publish
    python3 pipeline/make_overview.py --year 2025 --work w --out p \\
        --bbox 4,51,7,53 --jobs 4         # two UTM zones, for a smoke test
    python3 pipeline/make_overview.py --year 2025 --out p --thumbnail

Writes ``{out}/raster/{year}/overview.tif`` and, with ``--thumbnail``,
``thumbnail.webp`` beside it from the COG that is already there. Another
agent registers both as collection-level assets; this script only
produces files into the ``--out`` tree.

Ported from ``s2-mosaics-catalog/tools/make_overview.py``. The GDAL
environment, the per-(zone) VRT thread pool, the zone-at-a-time warp with
retries, nodata-as-0 through the whole VRT chain, the refusal to assemble
a mosaic with a hole in it, and the final COG creation options are
carried over unchanged -- every one of them was put there by a measured
failure on the s2 run, not by taste. What changed is the data: one
probability band instead of three reflectance bands, nine years instead
of thirty-six quarters, and a published index instead of a staging file.

What it reads, and why it is affordable
---------------------------------------
Nothing reads a full-resolution tile. Each source is opened through
``OVERVIEW_LEVEL``, so GDAL sees only that tile's internal overview: at
the default zoom 10 (152.87 m/px) the 64x overview at 160 m is the
coarsest level still within ``--oversample`` of the target, so a
40032 x 40032 tile is read as 626 x 626. One year is then

    7,466 tiles x 626 x 626 x 1 band x 1 byte = 2.9 GB

of pixel data, against 3.4 TB for the year at full resolution. The
script refuses to run at all if the chosen level is -1 (full
resolution), because that mistake costs 26 TB of reads rather than an
error message.

The three steps
---------------
1. One band-1 VRT per UTM zone over that zone's ``/vsicurl`` tiles, then
   a 3-band RGB VRT from ``gdaldem color-relief``. Zones are the unit
   because ``gdalbuildvrt`` needs one CRS and every tile is in its own
   UTM zone; the zone comes from the index's ``epsg`` column.
2. ``gdalwarp`` each zone's RGB VRT into EPSG:3857 on the exact
   Web-Mercator pixel grid of the chosen zoom level. These run in
   parallel, one process per zone, and a zone whose GeoTIFF already
   exists is skipped, so a job that ran out of time resumes where it
   stopped.
3. ``gdalbuildvrt`` over the warped zones, then one ``gdal_translate -of
   COG`` in 1024 px blocks on the zoom level's exact pixel grid (the
   ``-tap`` warps already pinned it).

512 px blocks, not GoogleMapsCompatible
---------------------------------------
The first build used ``TILING_SCHEME=GoogleMapsCompatible``, which pins
256 px blocks. At zoom 10 that is a 262,144 px wide base level in 413,696
tiles — doubled by the JPEG transparency mask's own tile set — so the
TileOffsets/ByteCounts arrays alone were 12-15 MB, and the Portolan
Browser spent ~480 range requests and 11.9 MB paging tile indexes before
it could draw much of anything (measured in DevTools, 2026-10-02: 38-41 kB
index reads alternating with 2-4 kB mask tiles at 350-920 ms each).
``BLOCKSIZE=512`` quarters the tile count (~103k base tiles, ~3-4 MB of
index across the whole pyramid, mask included) and is the Portolan
ceiling: PTL-DAT-013 (MUST, from OGC 21-026 /req/optimized_geotiff/
small-sizes) rejects internal tiles larger than 512. 1024 would halve the
index again but fails validation — raise it as a spec discussion, never
publish it. The remaining open cost is client-side fetch granularity
(geotiff.js's 64 KB default), tracked on the Portolan Browser. What the
scheme's loss gives up is XYZ-grid alignment, which only a tile server
slicing the COG directly would miss; the pixel grid itself is still the
zoom-10 grid from the ``-tap`` warps.

Zoom 10, for all nine years
---------------------------
The s2 catalog pinned its overview to zoom 9 because zoom 10 quadrupled
the read volume: its tiles carry a 32x overview, and zoom 9 could use it
where zoom 10 had to drop to the 16x. **That trade does not exist here.**
These tiles' coarsest overview is 64x at 160 m/px, and 160 m is within
1.1x of zoom 10's 152.87 m/px, so `overview_level()` picks the same 626 x
626 read at zoom 9 and at zoom 10. Zoom 10 is therefore free -- the same
2.9 GB of reads for four times the detail -- and it is what this script
defaults to.

It is one number for all nine years, like the colormap and the JPEG
quality, and for the same reason: the layer exists to be scrubbed through
time, and a year built at a different base resolution would make every
transition into or out of it look like a change in the scene rather than
in the resolution. Raising it means rebuilding all nine, not just the
next one.

The colormap
------------
The source is one band of field probability, quantised ``uint8 = p*255``,
so unlike the s2 layer there is nothing to make an RGB image out of. The
ramp is read from ``pipeline/style_bins.json`` -- the same approved
`field-prob` step expression the vector styles use, RdYlGn with score
edges 45/55/65/80 -- and laid out so that each approved colour falls
exactly on its approved score (see `browse_common.ramp`).

It is a continuous ramp rather than a hard 5-class colormap, for two
reasons. The raster is a probability surface, and a discrete colormap
turns a smooth surface into contour bands. More concretely, the overview
COG is JPEG-compressed, and JPEG rings at hard colour edges: a global
layer with four hard class boundaries would carry ringing artefacts along
every one of them. The ramp crosses each approved colour exactly at its
approved score, so a gradient legend with stops at 45/55/65/80 describes
it exactly.

One consequence to know about: the warp and the COG's internal overviews
average in RGB, not in probability, and RdYlGn is a diverging palette, so
a neighbourhood that is half red and half green averages to a muddy
brown rather than to the yellow that the mean probability would colour.
This was accepted rather than overlooked. The distribution is strongly
bimodal (see below) and everything below the floor is transparent and so
excluded from the average, so mixed red/green neighbourhoods are rare:
in the eight tiles measured, the fraction of pixels between DN 26 and DN
115 never exceeded 4% of the visible area. Colorising after the warp
instead would average in probability, at the cost of running gdaldem
over the full 262,144 x 262,144 mosaic.

What is transparent, and why it is not "probability 0"
-----------------------------------------------------
These COGs declare **no** nodata and 0 is real data: 0% field
probability, which is ocean and confident non-field land alike. So
something has to be chosen, and the obvious choice -- treat DN 0 as
nodata, the way the s2 layer treated all-zero reflectance -- was
measured and **rejected**. It is wrong in both directions at once.
Fraction of the coarsest overview that is exactly 0, measured
2026-10-01 over https on 2025 tiles:

    43RCQ_0_0  Indus desert, field_frac 0.00     72.3%  exactly 0
    38KQU_0_0  Madagascar coast, field_frac 0    46.9%  exactly 0
    50SQJ_0_0  Bohai coast, field_frac 0.00       0.5%  exactly 0  (max DN 20)

So DN 0 would punch transparent holes through three quarters of a desert
tile, and would leave a tile that is entirely water and entirely
field-free almost completely opaque, because the model emits small
non-zero probabilities over that water rather than exact zeros.

What the same measurement does show is that the distribution is sharply
bimodal, and that a floor anywhere in DN 26-51 separates the two modes
cleanly. Percentage of the 626 x 626 overview at or above each DN:

    tile        >=1    >=3    >=6   >=13   >=26   >=51  >=115   field_frac
    38KQU_0_0  53.1   36.8   22.8    3.5    0.0    0.0    0.0    0.000
    43RCQ_0_0  27.7   12.7    6.0    1.8    0.3    0.0    0.0    0.000
    50SQJ_0_0  99.5   69.0   18.9    0.0    0.0    0.0    0.0    0.000
    20MRC_0_0  42.8   24.7   19.5   16.0   14.0   12.4   10.3    0.100
    10TFL_0_0  37.6   17.7   13.2   10.5    8.9    7.5    5.5    0.051
    31UFU_0_0  79.0   69.0   52.1   34.4   29.7   25.8   18.3      --
    15TVH_0_0  97.3   96.2   95.4   94.2   92.7   90.3   83.1    0.810
    35QNE_0_0  99.5   99.1   98.8   98.4   97.9   97.1   95.5    0.951

The three field-free tiles are at or below 0.3% by DN 26; the five tiles
with fields barely move between DN 26 and DN 115, because their pixels
are either confidently field or confidently not. So:

**The browse layer is transparent below p = 0.10 (DN 26).** Water, bare
desert and closed forest drop out, the whole RdYlGn range including its
red stays visible, and nothing the catalog itself calls a field is
hidden -- the last column above shows that the index's own
``field_frac`` is the fraction of pixels at or above DN 115 (p = 0.45,
the first approved score edge), well above the floor.

The floor is carried by the colour table, which maps DN 0-25 to pure
black, and black is then the nodata value for the whole VRT chain. No
colour the ramp can produce is anywhere near black, and
`browse_common.ramp` asserts that, so the sentinel is unambiguous.

Nodata is 0 throughout, not an alpha band, and that is deliberate.
``gdalbuildvrt -srcnodata`` makes each source skip its nodata pixels, so
one zone's empty corner cannot erase the neighbouring zone's data where
their rectangles overlap -- which is exactly what an alpha band would
do, because a VRT paints sources in order and an alpha band is data.
**The overlap is real here, not hypothetical**: a self-join of
``index/raster.parquet`` for 2025 finds **3,183 pairs of tiles in
different UTM zones whose footprints overlap**, the largest by 2.04
degrees of longitude by 0.94 of latitude (34VFR_0_0 in zone 32634
against 35VLL_0_0 in 32635). On the way out, ``-b mask`` turns the
nodata mask into an alpha band, and the COG driver stores that alpha as
the internal transparency mask that JPEG compression requires.

Size
----
At zoom 10 the world is 262,144 px square, but the mosaic covers only
the FTW footprint. The COG is written **dense**, not sparse, and that was
measured the hard way on the s2 catalog: a sparse JPEG block reads as
"tile not found", and @developmentseed/geotiff -- the Portolan Browser's
renderer -- throws on it, which blanks the whole viewport batch. An empty
JPEG tile costs a few hundred bytes; a sparse one costs a client.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browse_common as bc  # noqa: E402

ZONE_ATTEMPTS = 4


def _zone_vrt(job: tuple[int, list[str], Path, int]) -> tuple[int, float, str]:
    """One zone's band-1 probability VRT. This is where the time goes.

    ``gdalbuildvrt`` opens every source to read its georeferencing, and
    inside one call those reads are serial: measured on the s2 catalog,
    a zone of 138 tiles took about 60 seconds for one band. The 79 zones
    of a year are independent, so they run on a pool.

    The source count is checked rather than trusted. ``gdalbuildvrt``
    **skips a source it cannot open and carries on**, so a burst of
    proxy denials would leave a quietly incomplete browse layer and
    nothing in the log. On the s2 catalog this was a manual check to run
    before trusting a quarter's overview; here it is a gate inside the
    build.
    """
    epsg, hrefs, scratch, level = job
    dest = scratch / f"{epsg}_p.vrt"
    listing = scratch / f"{epsg}.txt"
    listing.write_text("".join(bc.vsicurl(h) + "\n" for h in hrefs))
    if dest.is_file():
        return epsg, 0.0, ""
    t0 = time.monotonic()
    tmp = scratch / f".{epsg}_p.tmp.vrt"
    cmd = ["gdalbuildvrt", "-q", "-overwrite", "-b", "1",
           "-oo", f"OVERVIEW_LEVEL={level}",
           "-input_file_list", str(listing), str(tmp)]
    bc.run(cmd, f"gdalbuildvrt {epsg}")
    found = tmp.read_text().count("<SourceFilename")
    if found != len(hrefs):
        tmp.unlink(missing_ok=True)
        return epsg, time.monotonic() - t0, (
            f"{found} of {len(hrefs)} sources landed in the VRT; "
            "gdalbuildvrt skipped tiles it could not open, which would "
            "leave a hole in the overview")
    os.replace(tmp, dest)
    return epsg, time.monotonic() - t0, ""


def zone_rgb(epsg: int, scratch: Path, table: Path) -> Path:
    """Colourise one zone's probability VRT into an RGB VRT.

    Local VRT in, local VRT out: gdaldem reads nothing over the network
    here, so this costs nothing and is not worth a pool.
    """
    dest = scratch / f"{epsg}_rgb.vrt"
    if not dest.is_file():
        tmp = scratch / f".{epsg}_rgb.tmp.vrt"
        bc.run(["gdaldem", "color-relief", "-q", "-of", "VRT",
                str(scratch / f"{epsg}_p.vrt"), str(table), str(tmp)],
               f"gdaldem color-relief {epsg}")
        os.replace(tmp, dest)
    return dest


def zone_vrts(tiles: list[tuple[str, int, str]], scratch: Path, table: Path,
              level: int, jobs: int) -> dict[int, Path]:
    """One RGB VRT per UTM zone, colourised and ready to warp."""
    by_epsg: dict[int, list[str]] = {}
    for _tile, epsg, href in tiles:
        by_epsg.setdefault(epsg, []).append(href)

    work = [(epsg, group, scratch, level)
            for epsg, group in sorted(by_epsg.items())]
    t0 = time.monotonic()
    built, broken = 0, []
    with cf.ThreadPoolExecutor(jobs) as pool:
        for n, (epsg, secs, err) in enumerate(pool.map(_zone_vrt, work), 1):
            if err:
                broken.append((epsg, err))
                print(f"  zone {epsg}: {err}", file=sys.stderr)
            elif secs:
                built += 1
            if n % 10 == 0:
                bc.say(f"  {n}/{len(work)} zone VRT(s), "
                       f"{time.monotonic() - t0:,.0f}s elapsed")
    if broken:
        sys.exit(f"{len(broken)} zone VRT(s) are incomplete: "
                 f"{', '.join(str(e) for e, _ in broken)}. Nothing was "
                 "assembled; rerun to retry only these.")
    bc.say(f"  {len(work)} zone VRT(s) ({built} built, "
           f"{len(work) - built} reused), {time.monotonic() - t0:,.1f}s")
    return {epsg: zone_rgb(epsg, scratch, table)
            for epsg in sorted(by_epsg)}


def warp_zone(args: tuple[int, Path, Path, float]) -> tuple[int, float, str]:
    """One zone into EPSG:3857 on the zoom level's pixel grid.

    Retried, because the failure that actually happens is a truncated
    range read -- ``TIFFFillTile: got 117126 bytes, expected 153855``.
    The HTTP request succeeded with a 206, so GDAL's own
    ``GDAL_HTTP_MAX_RETRY`` never sees it; only redoing the warp
    recovers. A zone that fails every attempt is returned rather than
    raised: one flaky zone should not throw away the seventy-eight that
    worked, and the caller refuses to assemble an overview with a hole
    in it.
    """
    epsg, src, dst, res = args
    if dst.is_file():
        return epsg, 0.0, ""
    t0 = time.monotonic()
    tmp = dst.with_name(f".{dst.name}.tmp")
    last = ""
    for attempt in range(1, ZONE_ATTEMPTS + 1):
        tmp.unlink(missing_ok=True)
        r = subprocess.run(
            ["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:3857",
             "-tr", str(res), str(res), "-tap", "-r", "average",
             "-srcnodata", "0 0 0", "-dstnodata", "0 0 0",
             "-wo", "NUM_THREADS=2", "-multi",
             "-of", "GTiff", "-co", "TILED=YES", "-co", "COMPRESS=ZSTD",
             "-co", "BIGTIFF=IF_SAFER", str(src), str(tmp)],
            capture_output=True, text=True, env=bc.gdal_env())
        if r.returncode == 0 and tmp.is_file():
            os.replace(tmp, dst)
            return epsg, time.monotonic() - t0, ""
        last = (r.stderr or r.stdout)[-300:].strip()
        if attempt < ZONE_ATTEMPTS:
            bc.say(f"  zone {epsg}: attempt {attempt} failed, retrying "
                   f"({last.splitlines()[-1][:90] if last else 'no output'})")
            time.sleep(5 * attempt)
    tmp.unlink(missing_ok=True)
    return epsg, time.monotonic() - t0, last or "gdalwarp failed"


def build(year: int, index: str, work: Path, out: Path, zoom: int,
          oversample: float, jobs: int, bbox, min_prob: float,
          keep_scratch: bool, only: list[str] | None = None,
          stage_jobs: int = 0) -> Path:
    res = bc.resolution(zoom)
    level = bc.overview_level(res, oversample)
    if level < 0:
        sys.exit(f"zoom {zoom} ({res:,.2f} m/px) with --oversample "
                 f"{oversample} needs a source overview coarser than "
                 f"{bc.GSD * bc.FACTORS[0]:,.0f} m, which these tiles do "
                 "not have, so every tile would be read at full "
                 "resolution: 40032 x 40032 x 7,466 tiles, about 3.4 TB. "
                 "Refusing. Lower --zoom or raise --oversample.")
    # Staging is HTTP latency, not CPU, so it wants far more concurrency
    # than the warps do.
    stage_jobs = stage_jobs or min(64, jobs * 4)
    tiles = bc.tiles_of(index, year, bbox, only)
    if not tiles:
        sys.exit(f"no tiles match for {year}; nothing to build")
    zones = len({e for _t, e, _h in tiles})
    side = -(-bc.FULL_SHAPE // bc.FACTORS[level])   # ceil: 40032/64 is 626
    bc.say(f"{year}: {len(tiles):,} tile(s) over {zones} UTM zone(s), "
           f"zoom {zoom} ({res:,.2f} m/px), source overview level {level} "
           f"({bc.GSD * bc.FACTORS[level]:,.0f} m/px, {side} x {side} per "
           f"tile, {len(tiles) * side * side / 1e9:,.2f} GB of pixels)")

    part = out / "raster" / str(year)
    part.mkdir(parents=True, exist_ok=True)
    scratch = work / str(year) / "overview-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    # Black below the floor: the sentinel the whole VRT chain keys off.
    table = bc.write_color_table(scratch / "colors.txt", (0, 0, 0), min_prob)
    bc.say(f"  colour table {table}:\n    "
           + "\n    ".join(table.read_text().splitlines()))

    t0 = time.monotonic()
    vrts = zone_vrts(tiles, scratch, table, level, stage_jobs)
    bc.say(f"{year}: {len(vrts)} UTM zone(s) staged, "
           f"{time.monotonic() - t0:,.1f}s")

    t0 = time.monotonic()
    work_items = [(epsg, src, scratch / f"{epsg}_3857.tif", res)
                  for epsg, src in sorted(vrts.items())]
    broken = []
    with cf.ThreadPoolExecutor(jobs) as pool:
        for epsg, secs, err in pool.map(warp_zone, work_items):
            if err:
                broken.append((epsg, err))
                print(f"  zone {epsg}: FAILED after {ZONE_ATTEMPTS} "
                      f"attempts: {err}", file=sys.stderr)
            elif secs:
                bc.say(f"  zone {epsg}: warped in {secs:,.1f}s")
    if broken:
        # Every warped zone is kept, so a resubmit redoes only these.
        sys.exit(f"{len(broken)} zone(s) did not warp: "
                 f"{', '.join(str(e) for e, _ in broken)}. The overview "
                 "would have a hole in it, so it was not assembled; the "
                 "zones that succeeded are kept, so rerunning retries "
                 "only these.")
    bc.say(f"{year}: all zones warped, {time.monotonic() - t0:,.1f}s")

    mosaic = scratch / "mosaic.vrt"
    bc.run(["gdalbuildvrt", "-q", "-overwrite",
            "-srcnodata", "0 0 0", "-vrtnodata", "0 0 0", str(mosaic),
            *[str(p) for _, _, p, _ in work_items]], "gdalbuildvrt mosaic")

    # Nodata did its job in the mosaic; from here the transparency has to
    # be an alpha band. `-b mask` turns the nodata mask into a real fourth
    # band before the compression touches anything. The COG driver's own
    # ADD_ALPHA does not help: it only fires when the driver reprojects,
    # and by this point the mosaic is already on the tiling scheme's grid.
    rgba = scratch / "rgba.vrt"
    bc.run(["gdal_translate", "-q", "-of", "VRT",
            "-b", "1", "-b", "2", "-b", "3", "-b", "mask",
            "-colorinterp", "red,green,blue,alpha",
            str(mosaic), str(rgba)], "gdal_translate alpha")

    # Embedded band statistics are a Portolan MUST (PTL-DAT-009, with PAM
    # disabled, so a .aux.xml sidecar does not count). Computing them on the
    # source VRT writes PAM beside it, and the COG translate copies band
    # metadata into the file's GDAL_METADATA tag — verified with rashid's
    # own check. -approx_stats reads the warped zones' overviews, not every
    # base pixel, which the spec permits for statistics.
    t0 = time.monotonic()
    bc.run(["gdalinfo", "-approx_stats", str(rgba)], "band statistics")
    bc.say(f"{year}: band statistics computed, "
           f"{time.monotonic() - t0:,.1f}s")

    # JPEG, not WebP and not a lossless codec, and the choice is about the
    # readers and the bytes. JPEG-in-TIFF is the most widely decoded
    # compression there is: every GDAL build, every geotiff.js and all the
    # browser COG renderers read it. WebP-in-TIFF needs a GDAL built with
    # libwebp, and a reader without it shows an empty layer with no error.
    # The COG driver turns the alpha band into an internal transparency
    # mask, which mask-aware readers apply.
    #
    # Quality is one number for all nine years, like ZOOM and the
    # colormap: a year compressed differently would look different
    # mid-scrub.
    final = part / "overview.tif"
    tmp = final.with_name(f".{final.name}.tmp")
    t0 = time.monotonic()
    bc.run(["gdal_translate", "-q", "-of", "COG", str(rgba), str(tmp),
            "-co", "COMPRESS=JPEG",
            "-co", "OVERVIEW_COMPRESS=JPEG",
            "-co", "QUALITY=85",
            "-co", "OVERVIEW_QUALITY=85",
            # 512 is the Portolan ceiling (PTL-DAT-013): tiles MUST be
            # square and no larger than 512.
            "-co", f"BLOCKSIZE={os.environ.get('BLOCKSIZE', '512')}",
            "-co", "RESAMPLING=AVERAGE",
            "-co", "OVERVIEW_RESAMPLING=AVERAGE",
            "-co", "BIGTIFF=IF_SAFER",
            "-co", "NUM_THREADS=ALL_CPUS"], "gdal_translate COG")
    os.replace(tmp, final)
    bc.say(f"{year}: {final} written, JPEG+mask, "
           f"{final.stat().st_size / 1e6:,.1f} MB, "
           f"{time.monotonic() - t0:,.1f}s")
    if not keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return final


def thumbnail(source: Path, dest: Path, width: int, webp: bool) -> None:
    """Downsample the overview COG to one small image for the card.

    Reads the COG's own overviews rather than its base level, so this
    costs a few megabytes however large the COG is.
    """
    if not source.is_file():
        sys.exit(f"{source}: build the overview first")
    tmp = dest.with_name(f".{dest.name}.tmp")
    fmt = "WEBP" if webp else "JPEG"
    bc.run(["gdal_translate", "-q", "-of", fmt, "-outsize", str(width), "0",
            "-r", "average", str(source), str(tmp)],
           "gdal_translate thumbnail")
    os.replace(tmp, dest)
    # GDAL's WEBP driver writes the mask as a .msk sidecar (the format has
    # no alpha plane of its own), named after the tmp file.
    for stray in (dest.with_suffix(dest.suffix + ".aux.xml"),
                  tmp.with_suffix(tmp.suffix + ".aux.xml"),
                  dest.with_suffix(dest.suffix + ".msk"),
                  tmp.with_suffix(tmp.suffix + ".msk")):
        stray.unlink(missing_ok=True)
    bc.say(f"{dest}: {dest.stat().st_size / 1e3:,.0f} kB")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True, help="the publish tree; the "
                    "overview lands at {out}/raster/{year}/overview.tif")
    ap.add_argument("--work", help="scratch root for the per-zone VRTs and "
                    "warps (not needed with --thumbnail alone)")
    ap.add_argument("--index", default=bc.INDEX_URL,
                    help="raster.parquet, a URL or a local path; its `href` "
                         "column is the only source of object URLs "
                         f"(default {bc.INDEX_URL})")
    ap.add_argument("--zoom", type=int, default=10,
                    help="Web Mercator zoom level of the base resolution "
                         "(default 10, 152.87 m/px; fixed across years)")
    ap.add_argument("--oversample", type=float, default=1.1,
                    help="how much coarser than the target a source "
                         "overview may be (default 1.1)")
    ap.add_argument("--min-prob", type=float, default=bc.MIN_PROB,
                    help="field probability below which the browse layer is "
                         f"transparent (default {bc.MIN_PROB}; measured "
                         "ruling, see the module docstring)")
    ap.add_argument("--jobs", type=int, default=16,
                    help="zones warped at once (default 16)")
    ap.add_argument("--stage-jobs", type=int, default=0,
                    help="zone VRTs staged at once; these are HTTP header "
                         "reads, not computation, so the default is 4x "
                         "--jobs capped at 64")
    ap.add_argument("--bbox", help="west,south,east,north in degrees; keep "
                                   "only the tiles that meet it")
    ap.add_argument("--tiles", help="comma-separated tile_keys to build, "
                                    "instead of the whole year")
    ap.add_argument("--thumbnail", action="store_true",
                    help="write thumbnail.webp from the overview COG")
    ap.add_argument("--thumbnail-width", type=int, default=1024)
    ap.add_argument("--keep-scratch", action="store_true",
                    help="leave the per-zone VRTs and GeoTIFFs in place, "
                         "which is what makes a resubmit cheap")
    a = ap.parse_args(argv)

    caps = bc.require_gdal()
    webp = caps["webp"]
    if not webp:
        bc.say("this GDAL has no WEBP driver; the thumbnail falls back to "
               "JPEG. The overview COG is JPEG and does not need it.")
    part = Path(a.out) / "raster" / str(a.year)
    bbox = tuple(float(v) for v in a.bbox.split(",")) if a.bbox else None
    only = [t.strip() for t in a.tiles.split(",")] if a.tiles else None

    if a.work or only or bbox:
        build(a.year, a.index, Path(a.work or a.out), Path(a.out), a.zoom,
              a.oversample, a.jobs, bbox, a.min_prob, a.keep_scratch,
              only, a.stage_jobs)
    if a.thumbnail:
        thumbnail(part / "overview.tif",
                  part / ("thumbnail.webp" if webp else "thumbnail.jpg"),
                  a.thumbnail_width, webp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
