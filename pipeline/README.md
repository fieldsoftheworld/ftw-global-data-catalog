# The FTW 2nd Edition processing pipeline

This directory holds the code that turns Sentinel-2 imagery into the two
products the catalog publishes: 1,139,401,371 field polygons as per-UTM-zone
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
own tile's UTM zone, with average-resampled overviews at 4, 8, 16, 32 and 64
times the base pixel (10 to 160 m, down to 626 px; there is no 5 m level, which
would add about 40% to the file size for a resolution the 10 m inputs lack),
scale 1/255 and offset 0, and the tags `model`, `quantization`
(`uint8 = p*255`), `zstd_level` and `tile_key`. `run.py --layout hive` writes
the published `raster/` layout, `{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif`. Band 1 is
the field-interior probability and band 2 the field-boundary probability, so a
probability is the stored value divided by 255. No nodata value is declared,
and no land-cover or nodata mask is applied at this stage. These files are the
`raster/{year}/` product.

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
and then, on the unioned geometry, drops parts and parcels below 900 m², fills interior holes under 20 m²
(polygonizing 2.5 m rasters leaves half-pixel holes inside about 40% of
parcels; larger holes such as farm buildings are kept) and re-applies the
5 km² cap. The order matters. Filtering before the union
deleted fields that a seam had cut into two sub-minimum halves, and a cap
applied to the pre-union pixel area let an over-cap union through. All metric
work uses the zone's north UTM CRS, so the two halves of a field straddling
the equator stay comparable. Output is
`{year}/zone={NN}/utm{NN}.parquet`, sorted by Hilbert index, carrying fiboa
v0.3.0 metadata and the nine released columns. The staged file is validated
before publication: more rows out than in, or any duplicate parcel id, aborts
the run. `fill_small_holes.py` applies the same hole rule to zone files
converted before it existed, and `validate_vector.py` checks finished zone
files (nine columns, ZSTD, row-group size, geo CRS, sampled validity, area and
score ranges, hive layout, zone count) before they are published.

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

### Uploading through the Source Cooperative proxy

Quirks of `data.source.coop` seen while uploading the 2e products and
mirroring the mosaics. `upload_year.sbatch` uses `s3_put_retry.py` for them
(`PY` and `PROFILE` override the interpreter and the AWS profile).

- Single PUTs of about 170 MB and up answer 413. Use multipart with 16 MiB
  parts.
- A part can answer 520. botocore does not retry it and `aws s3 cp` then
  restarts the whole file, so a large file can fail every pass.
  `s3_put_retry.py` retries the failed part only and checks the remote size.
- Credentials are per-session STS keys that only work against the proxy
  (`AWS_PROFILE=source-coop AWS_ENDPOINT_URL=https://data.source.coop`).
- rclone fails with `SignatureDoesNotMatch` on `CreateMultipartUpload`
  (unresolved); aws-cli and boto3 are fine.
- The proxy 403s Python's default `Python-urllib` User-Agent. CDSE STAC search
  (the mosaic side) answers 429 under concurrent enumerations, so enumerate
  serially with jittered backoff.

## Stage 5: catalog and publish

`../tools/build_vector_index.py` and `../tools/build_raster_index.py` (with
`../tools/merge_raster_index.py` to fold in one year) write the
`index/vector.parquet` and `index/raster.parquet` manifests that list every
published file with its href, size and bbox, and
`../tools/build_raster_index_lite.py` writes `index/raster-lite.parquet`, the
110 kB tile finder (`year`, `tile_key`, `epsg`, float32 bbox, no hrefs). None
of them uploads. `../tools/build_vector_items.py` and
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
re-applies the cap to the unioned geometry. The cap is read from merge's
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

### Where the 2e release differs from this code

The published 2017-2025 files came from the production scripts this directory
was extracted from, so a rerun is not byte-identical.

- **Outline backend.** The release used `--backend fast` (`+q1`, the integer
  bucket-queue watershed) on windows cropped to their foreground, with a
  float16 lookup-table probability build that needs about 2 GB less transient
  memory per window. This code defaults to `exact` on the whole window; the
  cropped fast path is not ported because it depends on `fbp` internals.
- **Simplification.** The release used a Rust coverage simplifier (identical to
  GEOS 3.13.1 coverage simplify) at 5 m, with per-polygon Douglas-Peucker at
  1.2 m for the few parcels per tile that GEOS reports as coverage-invalid.
  This code keeps every polygon in one whole-coverage pass after repairing
  validity, so output differs only around those parcels.
- **Defects fixed here, not in the release.** See
  [Known differences from the released 2e data](#known-differences-from-the-released-2e-data).
- **Holes.** Interior holes under 20 m² are filled in all nine published years.
  The published descriptions' provenance string predates that.

### Known differences from the released 2e data

The released FTW 2nd Edition files were produced by the working pipeline before the fixes
below, and the data was **not regenerated** with them. This code is correct where they differ,
so reconverting with it changes the following (numbers measured on the released and raw
outputs; years whose raw outlines are partial are omitted, so counts are lower bounds there).

- **Four Norwegian tiles lose parcels.** Tile ownership tested the nominal 6 degree zone, but
  in MGRS band V zone 32 spans 3-12E (31V is 0-3E). Parcels west of 6E in tiles 32VKK, 32VKL,
  32VLK and 32VLL (south-west Norway: Bergen, Stavanger, Jaeren) were flagged outside zone 32,
  and no 31V tile exists, so merge dropped them. That is 10-19k parcels per year (2025: 16,681
  parcels, 63.7k ha). The released zone-32 file has no parcel west of 6E, and 32VKK and 32VKL
  contribute none. `outlines.lon_in_zone` has the exception.
- **About 7,000 parcels are unions above 5 km2.** The released conversion dropped parts and
  parcels under 900 m2 and applied the 5 km2 cap before the seam union, so two pieces under the
  cap could be published as one larger parcel. Five 2025 zones reconverted both ways (1.48M
  parcels) gave 9 such unions (49.8 km2) and no fields lost to two sub-900 m2 halves; scaled to
  the release that is on the order of 7,000 parcels, 0.06% of mapped area. Here every size
  filter runs once, after the union (`fiboa_common.final_select`).
- **A handful of equator seam pairs overlap.** Fields cut by the equator were not unioned
  because a per-row hemisphere CRS puts the two halves 10,000 km apart. In the 17 zones with
  tiles on both sides, the 2025 files have 5 overlapping pairs from different tiles, 3 of which
  the join rule would have merged (zones 17, 48 and 50). Areas are unaffected. This code
  projects each zone with one north-UTM CRS (`fiboa_common.zone_utm`).
- **The footers name the wrong method.** The released `determination:details` says
  BoundaryVote `(nbg-pb-h0.01-t0.3+A900)` for every year, while production ran
  `nbg-pb-h0.01-t0.3+R35+F10+G2+A900+q1` (`+G2` was added part-way through the 2018-2023
  runs, so the exact string can differ by year and is not recorded in the files). They also omit
  the 900 m2 rule and the 20 m2 hole fill. This code stamps the method that ran into the
  outline files, merge records it in `_summary.json`, and the footer states it, or says it was
  not recorded.
- **Not a difference.** The released tile ownership rounded the MGRS square from the raster
  origin; on the real 100.08 km rasters this gives the true square for all 7,467 tiles
  (including 59GQQ_0_1 and 60GTU_0_0), so no parcels moved. An earlier version of
  `outlines.mgrs_square` here assumed a 110 km raster and would have claimed a 90 km square;
  it now snaps the raster's north-west corner to its 100 km cell and has tests on real tile
  geometries.

Smaller, with no effect on the nine released columns: slope was stored as 0 where the DEM had
no coverage (QA columns in the intermediate files only), and the `bbox` struct was copied from
the source row, so about 0.03% of rows have a bbox slightly wider than the geometry (still a
superset).

### Where the published descriptions lag this code

This directory is the pipeline as it runs, and the catalog prose describes it
on those terms. Three strings in the published collection descriptions predate
it and will change the next time a year is rebuilt.

- The BoundaryVote spec here is `nbg-pb-h0.01-t0.3+R35+F10+G2+A900`, while
  every published collection records `nbg-pb-h0.01-t0.3+A900`.
- The band order here is B04/B03/B02/B08, while the published descriptions
  list B02/B03/B04/B08. The order in the code is the one the model requires,
  and `inference/run.py` refuses a stack that declares anything else.
- `fiboa_convert.py` writes "parcels and parts under 900 m2 removed", and no
  published description carries that sentence.

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
```

Run the inference tests with `-rs`, so the few that need a GPU skip loudly
rather than silently. The tests cover patch edges, blending, the COG and
band-order contracts, resume and provenance, and the model and device
preflights against real ONNX exports. CUDA throughput and parity against the
real checkpoint still need a GPU and the released model.
