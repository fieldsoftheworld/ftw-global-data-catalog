# The FTW 2nd Edition processing pipeline

This directory holds the code that turns Sentinel-2 imagery into the two
products the catalog publishes: 1,139,523,271 field polygons as per-UTM-zone
GeoParquet, and 67,197 field/boundary-probability COGs at 2.5 m, both covering
2017 through 2025. The published result is the
[FTW Global Data 2nd Edition](https://source.coop/ftw/global-data-2e) catalog,
and the repository around this directory is
[fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog).

Five stages run in order. Each writes files the next one reads, so a stage can
be rerun on its own once its inputs exist.

| # | Stage | Code | Input | Output |
|---|---|---|---|---|
| 1 | Quarterly mosaics | [`mosaics/`](mosaics/README.md) | CDSE `sentinel-2-global-mosaics` | 16-band 10 m GeoTIFF per tile |
| 2 | Model inference | [`inference/`](inference/README.md) | the 16-band stacks | two-band 2.5 m probability COG per tile |
| 3 | Polygon post-processing | [`postprocessing/`](postprocessing/README.md) | the probability COGs | `zone={NN}/utm{NN}.parquet` per year |
| 4 | Map tiles | this directory | the zone parquet | one PMTiles archive and one cell file per year |
| 5 | Catalog and publish | [`../tools/`](../tools/) | the published files | STAC metadata synced to the bucket |

Stage 2 produces the raster product the catalog publishes under `raster/`, and
stage 3 consumes the same COGs to produce the vector product under `vector/`.
The two are therefore two shapes of one prediction rather than two independent
measurements.

## Stage 1: quarterly mosaics

`mosaics/download.py` queries the public CDSE `sentinel-2-global-mosaics` STAC
collection, downloads the original band COGs through EODATA S3, and stacks
four quarters into one 16-band 10 m GeoTIFF per MGRS sub-tile. Each quarter
contributes B04, B03, B02 and B08 in that order, so the stack runs Q1 through
Q4 with red, green, blue and near-infrared inside each quarter. Reflectance
and nodata values are carried through without normalization, because stage 2
does its own scaling.

The run fails rather than proceeding on a missing quarter, a duplicate item, an
incomplete download, a grid mismatch, or a short STAC page. Every returned item
is checked against the query that produced it, and each failure names the
offending item id. A global run uses `--bbox -180 -90 180 90`, preferably with
`--tile-list` naming the cropland tiles to keep, and splits across jobs with
disjoint `--shard`/`--num-shards`.

`--index-output` also writes the B04 source index that stage 3 uses for quality
context, one row per tile and quarter. Running this stage needs CDSE S3
credentials in `EODATA_S3_ACCESS_KEY` and `EODATA_S3_SECRET_KEY`.

## Stage 2: model inference

`inference/run.py` runs the FTW `unet_balanced_fp32.onnx` model over each
16-band stack. Inputs are divided by 3000, upsampled four times with bilinear
interpolation, and cut into 512-pixel patches overlapping by 25%, which are
blended back together with a positive Hann window. The model emits background,
field and boundary logits, and the writer keeps the field and boundary
probabilities as uint8 with a scale of 1/255.

Each output is a ZSTD-compressed COG of 40,032 × 40,032 pixels at 2.5 m in its
own tile's UTM zone, with average-resampled overviews down to 626 px. Band 1 is
the field-interior probability and band 2 the field-boundary probability, so a
probability is the stored value divided by 255. No nodata value is declared,
and no land-cover or nodata mask is applied at this stage. These files are the
`raster/{year}/` product.

Every band carries its exact `STATISTICS_MINIMUM/MAXIMUM/MEAN/STDDEV` and a
`STATISTICS_VALID_PERCENT` of 100 embedded in the file itself, not in a PAM
sidecar — a Portolan MUST (PTL-DAT-009; PTL-DAT-010 for the valid percent).
They are computed from the array already in memory, so they are exact rather
than estimated and cost no extra pass over the pixels. The 2e tiles predate
this and are an accepted deviation (docs/conformance.md).

A stack that declares neither the `Q1_B04`…`Q4_B08` band descriptions nor the
`input_bands` tag is refused rather than assumed, because GDAL does not always
preserve band descriptions. The model SHA-256, the source raster tags and the
execution provider accompany every output and form part of the resume
fingerprint, so switching between CPU and GPU recomputes instead of silently
mixing devices. Running this stage needs the model checkpoint, which is
released separately and is not downloaded here.

## Stage 3: polygon post-processing

Four scripts turn probability rasters into released GeoParquet. They run in
order, and each writes a tree the next one reads.

`outlines.py` vectorizes the probabilities in windows with a core of 8,192
pixels and a halo of 512. It calls `fbp.methods.parse` with the BoundaryVote
instance method, so this one script needs the private `fbp` package described
below. Core centroid ownership decides which window keeps a parcel, which
holds down duplicates, though a parcel wider than the halo can still be
truncated or duplicated and is flagged with `touches_window_edge`. Ownership
follows the tile raster's own bounds and the MGRS longitude bands, including
the 31V/32V exception the Sentinel-2 grid uses.

This stage also attaches quality context to every parcel. `context.py` warps
the Copernicus DEM GLO-30 and the Impact Observatory 10 m annual land cover
over each window, and `outlines.py` reduces them to five per-parcel
attributes: `frac_water`, `frac_crops_ever`, `slope_mean`, `frac_slope_gt30`
and `elev_mean`. Land-cover vintages follow the product year through
`context.LULC_BY_YEAR`, so a 2020 product is never scored against 2024 land
cover, and a year absent from that table fails rather than borrowing another
year's vintages. None of these four attributes filters anything, and none of
them reaches the released file. See
[What is filtered, and what is not](#what-is-filtered-and-what-is-not).

`simplify_polygons.py` simplifies at 5 m in UTM across the whole coverage in
one pass, so edges shared between neighbors stay shared. Results are repaired
rather than re-simplified per geometry, and attributes survive. The Rust
implementation comes from the `coarsen` package on PyPI.

`merge_polygons.py` applies the only retention test in the pipeline,
`in_utm_zone AND in_mgrs_square AND area_m2 <= 5e6`, and writes one partition
per UTM zone. Zones resume on a fingerprint covering the input files and the
filter, so regenerated inputs and changed flags are rewritten rather than
skipped. A zone that retains nothing on a rerun has its partition removed, so
no stale generation survives into conversion. Missing inputs fail unless
`--allow-missing` is passed, and with it the summary records what was missing
and the next stage stamps "INCOMPLETE" into the metadata.

`fiboa_convert.py` repairs geometry, joins parcels split across tile seams,
and then, on the unioned geometry, fills interior rings under 20 m², drops
parts and parcels below 900 m² and re-applies the 5 km² cap. The order
matters. Filtering before the union deleted fields that a seam had cut into
two sub-minimum halves, and a cap applied to the pre-union pixel area let an
over-cap union through; the hole fill runs before both size tests, so a part
and a standalone parcel of the same shape are judged on the same area.
Polygonizing the 2.5 m probability raster leaves half-pixel (3.125 m²) holes
inside roughly 40% of parcels — 20 m² is about three pixels, and larger holes
(farm buildings, ponds, a neighbouring field) are kept. All metric
work uses the zone's north UTM CRS, so the two halves of a field straddling
the equator stay comparable. Output is
`{year}/zone={NN}/utm{NN}.parquet`, sorted by Hilbert index, carrying fiboa
v0.3.0 metadata and the nine released columns. The staged file is validated
before publication: more rows out than in, or any duplicate parcel id, aborts
the run.

## Stage 4: map tiles

The remaining scripts in this directory build one PMTiles archive per year on
the TGI RAILS Slurm cluster, using gpio and tylertoo. Each archive hands over
between two layers as you zoom in. Below z9 it draws a `cells` layer of A5
resolution-7 aggregates, every cell verbatim with no thinning. From z9 to z13
it draws the `fields` layer of actual parcels.

```bash
YEAR=2025 sbatch --export=ALL stage.sbatch           # 54 zone files -> one GeoParquet 2.0
YEAR=2025 sbatch --export=ALL aggregate_cells.sbatch # A5 r7 aggregates
YEAR=2025 sbatch --export=ALL tile_cells.sbatch      # the z0-8 cells archive
YEAR=2025 MODE=plan   sbatch --export=ALL tile_fields.sbatch
YEAR=2025 MODE=coarse sbatch --export=ALL tile_fields.sbatch
for i in $(seq 0 7); do YEAR=2025 MODE=shard IDX=$i sbatch --export=ALL tile_fields.sbatch; done
YEAR=2025 MODE=merge COARSE=fields-2025-a5r7.pmtiles sbatch --export=ALL tile_fields.sbatch
YEAR=2025 sbatch --export=ALL upload_year.sbatch     # upload, record size and multihash
```

Environment variables go through the shell with `--export=ALL`, because a
value inside `--export=A=x,B=y` gets comma-split by sbatch.

`aggregate_cells.sbatch` and `add_coverage.py` compute `count`, `area_ha`,
`avg_score` and `pct_covered` per cell. A5 is equal-area, so an r7 cell covers
about 2,075.5 km². The per-cell file is published alongside the archive as
`cells_a5r7_{year}.parquet`, and the four MapLibre styles in each collection
read these columns at low zoom and parcel columns above z9.

### Measured timings

Wall time per step on tylertoo main @ dabed9f, 2026-09-29, with queue waits
excluded. The coarse step runs `--plan-only`, which writes only the convert
plan and so needs neither the 192–360 GB nor the hours the old full-convert
coarse step took.

| step | 2024 (120.5 M) | 2025 (134.3 M) |
|---|---|---|
| stage (54 zones → GP2) | 51 m | 1 h 03 m |
| A5 r7 aggregate | 41 s | 40 s |
| cells archive z0–8 | seconds | seconds |
| shard plan | 1 s | 1 s |
| plan-only coarse | 6 m 31 s | 4 m 19 s (MaxRSS 22.6 GiB) |
| 4 shards z9–13 (parallel) | 12–24 m | 13–25 m |
| handover merge | 2 m 33 s | 3 m 57 s |
| **compute wall, stage→archive** | **≈ 1 h 25 m** (27.6 GB) | **≈ 1 h 35 m** (29.1 GB) |

### Cluster notes

These were all learned the hard way.

- `/tmp` is tmpfs and counts against the job cgroup, so `TMPDIR` points at
  `/u`. The scripts do this already.
- DuckDB `s3://` URLs hang on compute nodes, which blackhole the EC2 metadata
  probe. Use `https://data.source.coop/...`, and set a browser-like User-Agent
  for the list API, which rejects python-urllib with a 403.
- A plain DuckDB `COPY` of GeoParquet drops the `geo` metadata key. Merge with
  `gpio extract`, and follow any DuckDB rewrite with
  `gpio convert geoparquet ... --geoparquet-version 2.0`.
- tylertoo must be built on the cluster for glibc 2.28. Clone to
  `~/tylertoo-src` and run
  `PROTOC=/u/cholmes/micromamba/envs/ftw/bin/protoc cargo build --release`.
- GeoParquet 2.0 is what enables tylertoo's row-group pruning through native
  geo statistics, after which shard jobs read about 2% of row groups instead
  of the whole file.
- DuckDB's `ST_Area_Spheroid` is broken on A5 cell polygons, returning NaN or
  values 100× off, so `add_coverage.py` self-calibrates with pyproj. Dateline
  cells have vertices past ±180 and must be wrapped, or tile exporters drop
  them.


## Stage 4b: raster browse products

Two jobs turn the 67,197 probability COGs into things a browser can draw:
a **global overview COG per year** and a **thumbnail per item**. Both read
band 1 (field probability) and nothing else, both read only a source
tile's coarsest internal overview, and both colour it with the same ramp,
so an item card and the global layer say the same thing about the same
pixel.

| file | what |
|---|---|
| `browse_common.py` | the shared core: GDAL env, overview-level math, the colour ramp, index reads |
| `make_overview.py` + `overview.sbatch` | one `raster/{year}/overview.tif` + `thumbnail.webp` |
| `make_item_thumbnails.py` + `thumbnails.sbatch` | `raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.thumb.png`, as a Slurm array |

Tiles are **always** enumerated from `index/raster.parquet` and opened by
its `href` column. No key is ever constructed, so the scripts keep working
across the move to the grouped per-item folders
(`raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif`).
Use `href` (https), never `s3_href` — `browse_common.vsicurl()` refuses
the latter, because `s3://` hangs on a compute node.

### Running them

```bash
cp pipeline/* ~/ftw-beta-pipeline/ && cd ~/ftw-beta-pipeline

# Rehearsals first (minutes, from the login node or a job).
YEAR=2025 BBOX=4,51,7,53 JOBS=4 sbatch --export=ALL overview.sbatch
YEAR=2025 TILES=31UFU_0_0,32ULD_0_0 sbatch --export=ALL thumbnails.sbatch

# One year for real (all nine have been run this way).
YEAR=2025 sbatch --export=ALL overview.sbatch
YEAR=2025 sbatch --export=ALL --array=0-74%16 thumbnails.sbatch

for y in 2024 2023 2022 2021 2020 2019 2018 2017; do
  YEAR=$y sbatch --export=ALL overview.sbatch
  YEAR=$y sbatch --export=ALL --array=0-74%16 thumbnails.sbatch
done

# One failed array task, resubmitted alone. CHUNK must match the original
# run or the slice covers a different set of tiles.
YEAR=2025 sbatch --export=ALL --array=37 thumbnails.sbatch
```

Both are resumable and both keep what succeeded: the overview keeps each
zone's warped GeoTIFF in `WORK/<year>/overview-scratch` (that is what
`--keep-scratch` is for — do not clean it mid-build), and a thumbnail task
skips any PNG already on disk. A resubmit of either redoes only what is
missing. The overview **refuses to assemble a mosaic with a missing
zone**, so a partial run never produces a browse layer with a hole in it.
It also **refuses to publish a COG without embedded band statistics**: the
`gdalinfo -approx_stats` pass over the source VRT writes them to PAM, the COG
translate carries them into the file, and `verify_band_stats` re-reads the
finished file with PAM off before it is moved into place (PTL-DAT-009/010).

### Rulings, and what they rest on

- **Zoom 10** (152.87 m/px) for all nine years. The s2 catalog pinned its
  overview to zoom 9 because zoom 10 quadrupled its reads; that trade does
  not exist here. These tiles' coarsest overview is 64x at 160 m/px, which
  is within 1.1x of zoom 10, so the same 626x626 read serves zoom 9 and
  zoom 10 alike — 2.9 GB of pixels per year either way. What zoom 10 does
  cost is **file size** (see below). `ZOOM` is one env var, and it has to
  be identical across all nine years, so it is a decision to revisit
  before the first real build, not after.
- **Colormap**: the approved `field-prob` RdYlGn step expression from
  `style_bins.json`, read at run time, as a *continuous* ramp — `colors[0]`
  at the transparency floor and `colors[i+1]` exactly on `edges[i]`
  (score 45/55/65/80 → DN 115/140/166/204). Continuous rather than
  5-class because the overview is JPEG and JPEG rings at hard colour
  edges. A gradient legend with stops at those four scores describes it
  exactly.
- **Transparent below p = 0.10 (DN 26)**, *not* below DN 0. Treating
  probability 0 as nodata — the obvious port of the s2 layer's rule — was
  measured and rejected: it is wrong in both directions. 72.3% of a
  field-free Indus desert tile is exactly 0 (it would go full of holes)
  while only 0.5% of a field-free Bohai coast tile is (it would stay
  opaque, because the model emits small non-zero values over that water).
  What the measurement does show is a sharply bimodal distribution: the
  field-free tiles are at or below 0.3% of pixels by DN 26, and the tiles
  with fields barely change between DN 26 and DN 115. The full table is in
  `make_overview.py`'s docstring. The floor is carried by the colour table
  (DN 0–25 → black), and black is then the nodata value through the whole
  VRT chain.
- **Nodata is 0 through the VRT chain, never a mid-pipeline alpha band.**
  This is load-bearing, not precautionary: a self-join of
  `index/raster.parquet` finds **3,183 pairs of 2025 tiles in different
  UTM zones whose footprints overlap**, the largest by 2.04° of longitude.
  An alpha band is data, and a VRT paints sources in order, so one zone's
  empty corner would erase its neighbour's fields. Alpha appears once, at
  the end, via `-b mask`, and the COG driver stores it as the internal
  transparency mask JPEG needs.
- **Thumbnail width 512**, composited over `#0b1414` rather than masked
  (docs/plan.md Phase 4.2), so a tile with 5% field coverage reads as data
  on a dark card instead of as a mostly blank image. Measured cost over
  six real tiles: 256 px → 62 kB mean (4.2 GB for 67,197 items), 384 →
  131 kB (8.8 GB), **512 → 224 kB (15.0 GB)**, 626 → 323 kB (21.7 GB).
  `WIDTH` is an env var on the job.

### Known trade-offs

- **The overview averages in RGB, not in probability.** The warp and the
  COG's internal overviews both resample the colourised image, and RdYlGn
  is diverging, so a neighbourhood that is half red and half green
  averages toward brown rather than toward the pale yellow its mean
  probability would colour — roughly a one-bin pessimistic bias at coarse
  zooms. Accepted: the distribution is bimodal and everything below the
  floor is excluded from the average, so mixed neighbourhoods are mostly
  field interiors against their own boundaries. Avoiding it would mean
  colourising after the warp *and* giving up the COG driver's pyramid.
  The per-item thumbnails do not have this problem — they average the
  probability band first and colourise after.
- **Upstream: some tile edges carry spurious high probability over
  water.** Measured 2026-10-01 on the 64x overviews. On `31UET_0_0` the
  top four overview rows (the top ~640 m of the tile) average DN 109 over
  open North Sea where the interior at the same longitudes averages 7 —
  and the next four rows in are still at 80. It reproduces in **2017 as
  well as 2025**, so it is an inference edge effect in the source COGs,
  not a browse-pipeline artifact. It shows up in the browse layer as faint
  hairlines along some tile boundaries over water. It is **not** corrected
  here: same-zone tiles overlap by only 60–120 m, alternating seam by seam
  (at most 0.75 of an overview pixel),
  so insetting even one overview row to hide it would open visible gaps on
  the grid, which is worse. Four other tiles checked (38KQU, 50SQJ, 43RCQ,
  10TFL) show no edge anomaly, so it is edge- and tile-specific rather
  than universal. It reaches the **vector** product too, as of the
  MGRS-square ownership fix: the earlier square inset 5 km from the raster
  origin, which masked these outermost rows by accident, and the true
  100 km square comes within 80 m of the raster's own north edge. Nothing
  downstream filters them — `merge_polygons` keeps on `in_utm_zone AND
  in_mgrs_square AND area_m2 <= 5 km²`, and neither `frac_water` nor
  `frac_crops_ever` survives into the released columns — so the parcels
  these pixels produce are published. Dropping the outermost rows from
  `frac_water` at conversion time is the fix if it proves material.

### Expected costs

Measured on the login node, 2026-10-01, reading over https:

| | measured |
|---|---|
| one tile's 64x overview (626x626, band 1) | 393 kB, ~1.7 s cold |
| overview smoke, 11 tiles / 2 zones, zoom 10 | staging 3.4 s, warps 8.9 s, COG 1.0 s, **6.7 MB** |
| thumbnails, 4 tiles, 4 workers | 2.3 s |
| **one whole array task** (2025 task 74, 67 tiles, 8 workers) | **15.5 s**, so ~23 s for a full 100-tile task |

A compute node should beat that — it is in-region and the numbers above are
from the login node over the public endpoint.

Extrapolated for a full year (7,466 tiles, 79 zone/hemisphere CRSs — 54 UTM
zone partitions, some spanning both hemispheres). All nine years have since
been built and published; the estimates below are kept as the planning
record:

| step | estimate |
|---|---|
| pixel data read per year | **2.9 GB** (against 3.4 TB at full resolution) |
| overview staging (79 CRS VRTs, pool of 64) | 5–10 min; the largest zone is 246 tiles |
| overview warps (79 CRSs, 16 concurrent) | 20–40 min |
| final COG translate (dense JPEG, ~1.05 M blocks) | 15–45 min |
| **overview wall per year** | **~1–1.5 h**, 8 h requested |
| `overview.tif` size at zoom 10 | ~2–4 GB/year, ~20–35 GB for nine (zoom 9 would be ~4x smaller for the same reads) |
| thumbnails, one year | 75 array tasks x 100 tiles, ~23 s each; **~15 min wall at `%16`** |
| thumbnails, all nine years | 675 tasks, 26 GB read, **~15 GB written** across 67,197 objects |

Memory: `JOBS` (warp concurrency) is what sets peak RSS. The s2 catalog
measured 56.5 GB at 32 concurrent warps of a 3-band mosaic of this shape;
`JOBS=16` under `--mem=64G` leaves real headroom. If a year ever peaks
higher, **lower `JOBS` rather than raising `--mem`** — 120 G does not
schedule on this cluster under normal load, while 64 G starts in seconds.
The thumbnail tasks are tiny by comparison: 4 CPUs and 8 G each.

## Stage 5: catalog and publish

`../tools/rebuild_index.py` writes the `index/vector.parquet` and
`index/raster.parquet` manifests that list every published file with its href,
size and bbox. `../tools/build_vector_items.py` and
`../tools/build_raster_items.py` read those manifests and generate the STAC
tree under `../catalog/`, so counts and sizes in the metadata are measured
rather than retyped. `../tools/render_thumbnails.py` renders each collection's
thumbnail from its own published style, and `../tools/publish.py` syncs
`../catalog/` to the bucket. Edit a generator and rerun it; never edit its
output.

## What is filtered, and what is not

Land cover decides **where the pipeline runs**, and never **which parcels
survive**. Both halves matter, and conflating them is how the catalog ended up
with a wrong caveat.

Coverage comes first. Stage 1 runs against a cropland tile list, described at
`mosaics/README.md`, and that list is the intended tile set: a named tile the
STAC query does not return fails the run. The threshold shows plainly in the
published index. Every year's minimum `cropland_frac` is exactly 0.010006 and
no tile falls below 1%, which is the signature of a cut rather than of a
natural distribution. Tiles under 1% cropland were never processed, so open
desert, ice, dense forest and purely urban areas are absent by construction.
An empty region in this data means the pipeline never ran there.

Within a processed tile, a parcel is removed for exactly three reasons, none
of them land cover. The retention test in `merge_polygons.py` is
`in_utm_zone AND in_mgrs_square AND area_m2 <= 5e6`, which drops parcels a
neighboring tile owns and parcels over the area cap. `fiboa_convert.py` then
drops parts and whole parcels under 900 m² after the seam union, and
re-applies the cap to the unioned geometry. One geometry change is not a
removal: interior rings under 20 m² are filled rather than published, so a
parcel keeps its outline but loses the pixel-scale holes polygonization left
in it. Nothing is removed by that step. The cap is read from merge's
`_summary.json` rather than hard-coded, so it traces back to merge's
`--max-km2`; the 5 km² constant in `fiboa_convert.py` is only the fallback for
a missing summary.

Land cover and terrain are read per parcel, but only to describe it. The five
attributes they produce, `frac_water`, `frac_crops_ever`, `slope_mean`,
`frac_slope_gt30` and `elev_mean`, appear in no retention test anywhere in the
pipeline, and none of them is published: the released projection in
`fiboa_common.py` selects nine columns, and `write_sorted` casts to that
schema, so anything else is dropped structurally. Water, scrub and built-up
ground can therefore carry predicted parcels, and a user who needs them gone
has to mask them downstream without the attributes that would have helped.

The `score` column is the mean model field probability inside the parcel,
multiplied by 100 and rounded into a uint8. It ranks parcels and is not
calibrated against ground truth, so a score of 80 is not an 80% chance that the
parcel is real. It is still the best filter available in the released schema
for trading precision against recall.

## Reproducing this

Stages 1, 4 and 5 run on public inputs with public tools. Stage 2 needs the FTW
model checkpoint, which is released separately by the
[FTW project](https://fieldsofthe.world) along with its model card. Stage 1
needs CDSE S3 credentials, which any user can obtain through
[CDSE S3 access](https://documentation.dataspace.copernicus.eu/APIs/S3.html).

Stage 3 is the one gap. `outlines.py` calls `fbp.methods.parse`, and no
BoundaryVote implementation is vendored here. The private `fbp` package is not
on PyPI, and it is not the unrelated `fbp` 1.3.6 published there, which does
not expose `fbp.methods`. It must provide the production method
`nbg-pb-h0.01-t0.3+R35+F10+G2+A900`; `--backend fast` adds `+q1` and needs that
variant. Until the package is released, the outline stage requires separately
authorized access, and it fails with one message naming that need rather than
once per tile. The other three post-processing scripts run without it.

### The 2026-10 rerun (r1) is not this code

All nine years were regenerated by the production pipeline (`taylor-geospatial/global-ftw-2e`,
run r1) with BoundaryVote `nbg-pb-h0.01-t0.5+R25+F10+G2+A900+q1`, the orphan-strip claim rule,
the 31V/32V band exception, inland water (`frac_water >= 0.7`) and sea (OSM land under 50%)
removal and the 5 km2 cap after the seam union. The port here still implements `t0.3+R35`
without the claim rule or the water and sea rules, so never run its `merge_polygons.py` or
`fiboa_convert.py` on r1 outputs; each year's footer (`determination:details`) states what
produced it.

### Where the published descriptions lag this code

This directory is the pipeline as it runs, and the catalog prose describes it
on those terms. Four strings in the published collection descriptions predate
it and will change the next time a year is rebuilt.

- Every published collection records the BoundaryVote spec
  `nbg-pb-h0.01-t0.3+A900`, and that id is no longer written from a constant.
  `outlines.py` stamps `{spec, backend}` into each tile's `outline_provenance`,
  `merge_polygons.py` unions those into `_summary.json`'s `spec`/`specs` (with
  `tiles_unrecorded_spec` counting the tiles that carry no id), and
  `fiboa_convert.py` writes whatever is there. So a year rebuilt from the outline
  stage under `--backend fast` records
  `nbg-pb-h0.01-t0.3+R35+F10+G2+A900+q1` — the `+q1` the old constant omitted —
  while a year merged from the existing pre-stamp outlines reads `method id not
  recorded in the run` for them, since resume keeps those tiles as current and
  never restamps them. `fiboa_convert --spec nbg-pb-h0.01-t0.3+A900` states the
  known id for that case, marked as supplied rather than recorded.
- The band order here is B04/B03/B02/B08, while the published descriptions
  list B02/B03/B04/B08. The order in the code is the one the model requires,
  and `inference/run.py` refuses a stack that declares anything else.
- `fiboa_convert.py` writes "parcels and parts under 900 m2 removed", and no
  published description carries that sentence.
- `fiboa_convert.py` writes "interior holes under 20 m2 filled", and no
  published description carries that sentence either, although the released
  files were filled: every interior ring measured in the published 2017, 2021,
  2024 and 2025 zone files is at least 21.875 m² (3.5 px at 2.5 m), with none
  below the 20 m² threshold. The rule was in force when the data was made; only
  the prose predates it. Both this bullet and the one above resolve the same
  way — the next rebuild stamps the real sentence into the parquet footer, and
  `tools/build_vector_items.py` regenerates the descriptions from it.

All nine collections carry byte-identical provenance text, so it records the
revision that wrote the metadata rather than how any individual year was
processed. Do not read a per-year difference into it.

`context.LULC_BY_YEAR` defines land-cover vintages for 2020, 2024 and 2025
only, and raises on any other year rather than borrowing a mismatched one. Add
rows for 2017–2019 and 2021–2023 before rerunning those years.

```bash
uv venv
uv pip install -r pipeline/postprocessing/requirements.txt
uv pip install pytest
.venv/bin/python -m pytest pipeline/postprocessing/tests
.venv/bin/python -m pytest -rs pipeline/inference/test_inference.py
.venv/bin/python -m pytest pipeline/mosaics/test_mosaics.py
.venv/bin/python -m pytest -rs pipeline/test_make_overview.py
```

Run the inference tests with `-rs`, so the few that need a GPU skip loudly
rather than silently. The tests cover patch edges, blending, the COG and
band-order contracts, embedded band statistics, resume and provenance, and the
model and device preflights against real ONNX exports. CUDA throughput and
parity against the real checkpoint still need a GPU and the released model.
The rashid cross-check in `test_inference.py` and the end-to-end overview
statistics test skip loudly too — the first wants rashid importable, the second
GDAL's command-line tools on PATH — so run both with `-rs` as well.
