# Beta PMTiles pipeline (rails)

GeoParquet → PMTiles for the beta field predictions, run on the TGI RAILS
Slurm cluster with gpio + tylertoo. One archive per year with a zoom
handover:

- **z0–8 `cells` layer** — a5 r7 aggregates, every cell verbatim.
- **z9–13 `fields` layer** — every predicted field, all parquet columns.

## Status

These scripts are the proven alpha pipeline, copied verbatim from
`fieldsoftheworld/ftw-data-catalog@fd844c4` (`scripts/tiles/`), which built
`predictions/vectors-test/fields-yearly` from the 1.58 B-feature alpha
release. They are the starting point, not yet the beta pipeline. Adapting
them to the beta source (Phase 3 of [docs/plan.md](../docs/plan.md)) changes:

- **Stage** (`stage_global.py`): read `vector/{year}/utm{NN}.parquet` from
  `ftw/global-data-beta` instead of the alpha `results-by-admin-conf`
  partitions; carry **all** parquet columns (drop only the `bbox` struct and
  constant columns from tiles); measure cross-zone duplicates before deciding
  to dedupe (beta ids are tile-scoped; the alpha `(id, area)` dedupe targeted
  a different defect); no year split (the source is already per-year); no
  `SET TimeZone` need expected (no datetime column — verify).
- **Aggregate** (`aggregate_cells.sbatch` + `add_coverage.py`): metrics from
  the beta schema — `count`, `area_ha` from `metrics:area`, `avg_field_prob`,
  `avg_boundary_prob`, `pct_covered`. The 350 km² giant-field cutoff should
  be unnecessary (beta post-processing already removes >5 km² parcels —
  verify the max first).
- **Tile/shard** (`tile_cells.sbatch`, `tile_fields.sbatch`): beta is ~12×
  smaller than alpha (134 M vs 1.58 B features for 2025), so expect hours,
  not days, possibly fewer shards, and maybe no 360 G coarse node.

The measured reference points below are from the alpha run and bound the
beta run from above.

## Running it (alpha shape, for reference)

```bash
# 1. Stage, merge, convert to GeoParquet 2.0
sbatch stage.sbatch

# 2. Cell archive
YEAR=2025 sbatch --export=ALL aggregate_cells.sbatch
YEAR=2025 sbatch --export=ALL tile_cells.sbatch     # prints tile-weight report

# 3. Field shards + handover merge
export IN=$PWD/global2025_gp2.parquet
YEAR=2025 MODE=plan   sbatch --export=ALL tile_fields.sbatch
YEAR=2025 MODE=coarse sbatch --export=ALL --mem=360G tile_fields.sbatch
for i in $(seq 0 7); do YEAR=2025 MODE=shard IDX=$i sbatch --export=ALL tile_fields.sbatch; done
YEAR=2025 MODE=merge COARSE=fields-2025-a5r7.pmtiles sbatch --export=ALL tile_fields.sbatch
```

Env vars go through the shell + `--export=ALL` (a value inside
`--export=A=x,B=y` gets comma-split by sbatch).

## Cluster gotchas (all learned the hard way on alpha)

- `/tmp` is tmpfs and counts against the job cgroup — always point `TMPDIR`
  at `/u` (the scripts do).
- DuckDB `s3://` URLs hang on compute nodes (blackholed EC2 metadata probe);
  use `https://data.source.coop/...`, and set a browser-like User-Agent for
  the list API (it 403s python-urllib).
- A plain DuckDB `COPY` of GeoParquet drops the `geo` metadata key — merge
  with `gpio extract`, and follow any DuckDB rewrite with
  `gpio convert geoparquet ... --geoparquet-version 2.0`.
- tylertoo must be built ON the cluster (glibc 2.28): clone to
  `~/tylertoo-src`, `PROTOC=/u/cholmes/micromamba/envs/ftw/bin/protoc cargo
  build --release`.
- GeoParquet 2.0 conversion is what enables tylertoo's row-group pruning
  (native geo stats) — shard jobs then read ~2% of row groups instead of the
  whole file.
- a5 is equal-area: r7 ≈ 2,075.5 km² per cell. DuckDB's `ST_Area_Spheroid`
  is broken on a5 cell polygons (NaN or 100× off) — `add_coverage.py`
  self-calibrates with pyproj. Dateline cells have vertices past ±180 and
  must be wrapped or tile exporters drop them.

## Measured timings (beta, tylertoo main @ dabed9f, 2026-09-29)

Wall time per step (Slurm sacct; queue waits excluded). The coarse step is
`--plan-only` (tylertoo #541/#574): it writes only the convert plan, so it
needs neither the 192–360 G of the old full-convert coarse nor its hours.

| step | 2024 (120.5 M) | 2025 (134.3 M) |
|---|---|---|
| stage (54 zones → GP2) | 51 m | 1 h 03 m |
| a5 r7 aggregate | 41 s | 40 s |
| cells archive z0–8 | seconds | seconds |
| shard plan | 1 s | 1 s |
| plan-only coarse | 6 m 31 s | 4 m 19 s (MaxRSS 22.6 GiB) |
| 4 shards z9–13 (parallel) | 12–24 m | 13–25 m |
| handover merge | 2 m 33 s | 3 m 57 s |
| **compute wall, stage→archive** | **≈ 1 h 25 m** (27.6 GB) | **≈ 1 h 35 m** (29.1 GB) |

Reference: the same 2025 build on tylertoo 6b9c3ff (pre plan-only, 17-column
schema) took 37 m 44 s at 192 GiB MaxRSS for the coarse step alone and
produced a 44.1 GB archive. The alpha run (1.58 B features) took ~7 h 50 m
for its 115.5 GB archive with a 360 G coarse node.

## Raster browse products (Phase 4)

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
| `make_item_thumbnails.py` + `thumbnails.sbatch` | `raster/{year}/{tile}/{tile}.thumb.png`, as a Slurm array |

Tiles are **always** enumerated from `index/raster.parquet` and opened by
its `href` column. No key is ever constructed, so the scripts keep working
across the move to per-item folders (`raster/{year}/{tile}/{tile}.tif`).
Use `href` (https), never `s3_href` — `browse_common.vsicurl()` refuses
the latter, because `s3://` hangs on a compute node.

### Running them

```bash
cp pipeline/* ~/ftw-beta-pipeline/ && cd ~/ftw-beta-pipeline

# Rehearsals first (minutes, from the login node or a job).
YEAR=2025 BBOX=4,51,7,53 JOBS=4 sbatch --export=ALL overview.sbatch
YEAR=2025 TILES=31UFU_0_0,32ULD_0_0 sbatch --export=ALL thumbnails.sbatch

# 2025 for real, then the other eight once its size and timings are seen.
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
  here: same-zone tiles overlap by only 80 m total (0.5 overview pixels),
  so insetting even one overview row to hide it would open visible gaps on
  the grid, which is worse. Four other tiles checked (38KQU, 50SQJ, 43RCQ,
  10TFL) show no edge anomaly, so it is edge- and tile-specific rather
  than universal.

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

Extrapolated for a full year (7,466 tiles, 79 UTM zones) — **measure 2025
before fanning out to the other eight**:

| step | estimate |
|---|---|
| pixel data read per year | **2.9 GB** (against 3.4 TB at full resolution) |
| overview staging (79 zone VRTs, pool of 64) | 5–10 min; the largest zone is 246 tiles |
| overview warps (79 zones, 16 concurrent) | 20–40 min |
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

## Mosaic input preparation

[Quarterly mosaic downloads](mosaics/README.md) assemble 16-band input tiles.

## Model inference

[Quarterly-mosaic inference](inference/README.md) produces probability COGs.

## Polygon postprocessing

[Parcel outline and release pipeline](postprocessing/README.md) uses the external fbp package.
