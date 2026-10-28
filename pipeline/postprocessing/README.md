# Polygon postprocessing

This directory turns the 2.5 m field/boundary probability COGs that inference writes
into the per-UTM-zone fiboa GeoParquet the catalog publishes under `vector/`. Four
stages run in order, and each writes a tree the next one reads:

1. `outlines.py` vectorizes each tile into parcel outlines and attaches ownership flags
   and quality context to every parcel.
2. `simplify_polygons.py` simplifies each tile's outlines as one coverage.
3. `merge_polygons.py` keeps the parcels each tile owns, drops oversized and water
   parcels, writes one file per UTM zone and then runs `cross_zone_seams.py`.
4. `fiboa_convert.py` joins parcels split by tile seams, applies the size and sea rules
   and writes the released zone files. `fiboa_ranges.py` does the same work in parts
   for large zones.

The map-tile stage in [`pipeline/`](../README.md#stage-4-map-tiles) reads the released
zone files.

```sh
uv venv
uv pip install -r pipeline/postprocessing/requirements.txt
```

`outlines.py` runs `fbp.FTW_2E` from [`ftw-fbp`](https://pypi.org/project/ftw-fbp/)
(installed by the requirements, imported as `fbp`). It is the method of the published
2017-2025 vectors, whose footers record it as `nbg-pb-h0.01-t0.5+R25+F10+G2+A900+q1`.
The id stands for non-background pixels as foreground,
watershed seeds from the boundary surface at depth 0.01, a watershed line kept
when half of its pixels have p(boundary) >= p(field) (`t0.5`), regions with mean
p(field) under 25% dropped (`R25`), parcels of the p(field) > 10% mask that the
result covers less than half re-adopted (`F10`) unless under 2% of that mask is
uncovered (`G2`), and parcels under 900 m² dropped (`A900`). It runs with
`fast=True` (`+q1`), an integer h-maxima and bucket-queue watershed on the 1/255 grid
the scores are stored on, as the published run did. The method id is part of each
tile's resume fingerprint, so an outline tile written under another id is recomputed.
Only `outlines.py` needs fbp; the other stages run without it. It polygonizes the parcels with
[`contourrs`](https://pypi.org/project/contourrs/), whose polygons are identical to
`rasterio.features.shapes` at 4-connectivity.

```sh
.venv/bin/python pipeline/postprocessing/outlines.py --year 2025 \
  --scores staging-data/raster --out-root outlines --index-dir index --workers 1
.venv/bin/python pipeline/postprocessing/simplify_polygons.py --year 2025 \
  --in-root outlines --out-root simplified --workers 1
.venv/bin/python pipeline/postprocessing/merge_polygons.py --year 2025 \
  --in-root simplified --out-root merged --no-aux --tmp-dir scratch/duckdb \
  --keep-list tiles.txt --empty-list empty.txt   # ends with cross_zone_seams.py
# once: OSM land polygons for the sea rule (osmdata.openstreetmap.de, ODbL)
curl -LO https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip
unzip land-polygons-split-4326.zip
.venv/bin/python pipeline/postprocessing/sea_filter.py \
  land-polygons-split-4326/land_polygons.shp land_polygons.parquet
.venv/bin/python pipeline/postprocessing/fiboa_convert.py --year 2025 \
  --in-root merged --out-root fiboa --tmp-dir scratch/duckdb --zone 15 \
  --land-polygons land_polygons.parquet
```

`fiboa_convert.py` converts one zone per call (`--zone NN`, or `--zone-index i` into the
year's sorted zone list for array jobs). To run conversion offline, install DuckDB's
spatial extension once beforehand (`INSTALL spatial`).

## Inputs

Score COGs are read from `{scores}/{year}/{tile}/{tile}.tif` or from the grouped
`{scores}/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif`. These are the two layouts inference
writes, and both are discovered, so a partly migrated tree needs no flag. Each COG has
two uint8 bands (field, boundary) holding probability × 255 on a north-up UTM grid at
2.5 m.

The QA context needs `{index-dir}/tile_index_{year}*.parquet` with the columns
`tile_key`, `quarter`, `b04_s3_href` and `b04_s3_endpoint`, one row for each of a tile's
four source mosaic B04 objects. `mosaics/download.py --index-output` writes this index.
`b04_s3_href` is an `s3://bucket/key` on CDSE EODATA, opened as `/vsis3/bucket/key`
against the endpoint the row records, so set `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` to your EODATA keys first. The index also carries
`b04_odata_href`, which is OIDC-authenticated and cannot be opened by GDAL, so it is kept
for provenance only. These assets have no public unauthenticated https form, so there is
no fallback.

Terrain context reads the public Copernicus DEM GLO-30 and Impact Observatory (IO)
annual land cover over https. The land-cover vintages for `frac_crops_ever` follow the
product year (`context.LULC_BY_YEAR`), and a parcel counts as cropland if it is crop in
any sampled vintage, so a 2020 product is never scored against 2024 land cover. A year
missing from that table fails rather than borrowing another year's vintages.
`frac_water` always uses the 2024 vintage (see [Merge](#merge)).

`merge_polygons.py` needs two tile lists. `--keep-list` (`tiles.txt`) names the expected
tiles and `--empty-list` (`empty.txt`) names verified empty or excluded tiles.
`ownership.py squares` and the north-edge check in `qa_vector.py` read tile footprints,
which `pipeline/mosaics/keeplist.py bbox --out tile_footprints.parquet` writes.

## Outlines

`outlines.py` processes each tile in windows with a core of 8,192 pixels and a halo of
512 (`--core`, `--halo`). fbp keeps each parcel in the window whose core holds its
centroid, which limits duplicates, but a parcel wider than the halo can still be
truncated or duplicated. `touches_window_edge` marks those candidates, and conversion
joins seam parcels.

A tile keeps a parcel whose centroid lies in its UTM zone (`in_utm_zone`) and in its
100 km MGRS square (`in_mgrs_square`). The zone test uses the MGRS longitude bands,
including the 31V/32V exception the Sentinel-2 grid follows. Near some zone edges the
mosaic product has no tile for a partial square, so a tile also keeps (`claimed`) a
parcel of its own square that lies in such a square of the adjacent zone. `ownership.py`
documents that rule, and `cross_zone_seams.py` unions the overlapping pieces after merge.

The square is the 100 km cell that the north-west corner of the tile raster snaps to,
so it follows the pixels and not the tile key. For a stacked `_r_c` sub-tile it is not
the cell the key names (59GQQ_0_1 owns the 59GQP cell). This has two known, measured
consequences:

- Stacked sub-tiles claim abutting squares whose shared edge sits 80 m north of where
  their rasters abut, so an 80 m × 100 km strip (~8 km², nine items) is owned by
  neither.
- Neighbouring squares abut exactly, so parcels on a tile boundary depend on the
  cross-tile seam union. Such a parcel is truncated at each raster's own data edge,
  which `touches_window_edge` does not flag because it marks only window borders inside
  the raster, and each half is centred in its own square, so both halves are owned.
  Same-zone rasters overlap by only 40–120 m, while `MIN_OVERLAP` requires 10% of the
  smaller half, so a field reaching more than ~1.2 km each side of a same-zone seam is
  published as two overlapping halves.
  `test_a_wide_field_across_a_same_zone_seam_is_not_rejoined` records this case. The
  cross-tile rule should be loosened for shared tile boundaries before the next
  generation runs.

Each outline tile resumes on a fingerprint of the source COG's identity, the flags,
`OWNERSHIP_RULES` and the owned-squares file. A change to which parcels a tile claims
therefore rewrites every tile on the next run instead of resuming onto parquet written
under the previous rule. Bump `OWNERSHIP_RULES` with every change to `in_utm_zone`,
`in_mgrs_square` or `claimed`. `--owned-squares` names the squares of the run's
keep-list tiles (`ownership.py squares` builds the file), and the default
`owned_squares.csv` is the current 14,608-tile keep-list. Empty outline tiles are
written as readable empty Parquet files.

## Simplification

`simplify_polygons.py` simplifies at 5 m (`--tolerance-m`) in UTM over each tile's
whole coverage in one pass, so shared edges stay shared. Results are repaired, never
re-simplified per geometry, and attributes are kept. `area_m2` and `n_parts` are
recomputed from the simplified geometry. The coverage simplification comes from the
`coarsen` package on PyPI (a Rust implementation), called through its Python API.

## Merge

`merge_polygons.py` keeps `in_utm_zone AND in_mgrs_square AND area_m² <= 5 km² AND
frac_water < 0.7`. Because simplification refreshes `area_m2`, the cap and the summary
totals describe the geometry actually written.

`frac_water` is the share of a parcel's pixels that are water in the IO io-lulc
2024 layer, for every product year (`context.WATER_VINTAGE`), so a reservoir that
filled or drained between 2017 and 2025 is judged by its 2024 state. The rule removes
inland water, aquaculture ponds and salt pans (2025: 1.91M parcels at 0.7, 2.01M at
0.5). The threshold is 0.7 and not 0.5 because rice paddies are flooded in one quarter
and vegetated in another. In 20-chip samples on false-colour Sentinel-2, [0.5, 0.7) held
9 crop fields to 6 ponds and [0.7, 0.9) held 2 fields to 12 ponds.
`--max-frac-water 2` keeps all water parcels, and the value is part of the merge
fingerprint and `_summary.json`.

`tiles.txt` and `empty.txt` are disjoint, and an input tile in neither is refused.
Missing inputs fail unless `--allow-missing` is passed. With it, `_summary.json`
records `allow_missing`, the missing list and the expected and present counts, and
`fiboa_convert.py` writes "INCOMPLETE" into `determination:details` so an incomplete
release can be identified.

The optional patch-statistics join reads `--aux-root`; pass `--no-aux` when no
auxiliary parcel table exists. Each tile's aux file is matched exactly
(`{aux}/{tile}.parquet`, never a zone prefix glob) and checked for uniqueness on
`(tile_key, parcel_id)` before the join. A zone with no aux file joins NULL rather
than failing, so every zone file in a year has the same schema. The written row count
is compared with the retained count before the join, so a fan-out cannot reach the
release.

Zones resume on a `merge_fingerprint` covering the input files' identity and the
filter, so regenerated inputs and changed flags are rewritten rather than skipped, and
`--force` rewrites regardless. A zone that retains nothing on a rerun has its partition
removed, so no stale generation reaches the zone discovery in `fiboa_convert.py`. DuckDB
spills to `--tmp-dir` (pid-scoped, outside `--out-root`), and the per-zone temp file is
`part-0.parquet.tmp-<pid>`, which no `*.parquet` glob matches. Merge ends by running
`cross_zone_seams.py` on the zones it wrote (`--no-cross-zone` skips it).

## Conversion

`fiboa_convert.py` repairs geometry and joins seams. Then, on the unioned geometry, it
fills interior rings under 20 m², drops parts and parcels below 900 m², re-applies
merge's km² cap, computes area and perimeter, and derives the bbox covering.

The sea rule (`sea_filter.py`) then drops parcels with less than half their area on
OSM land polygons. io-lulc cannot do this because it has no data over open sea and
counts nodata as dry (south-west Norway 2025: 3,361 offshore parcels with a median
`frac_water` of 0). The OSM polygons are derived from the coastline, so islands,
polders and lakes are land, while tidal flats outside the coastline and the Caspian
are sea. `--land-polygons` (or `$FTW_LAND_POLYGONS`) names the prepared GeoParquet and
`--no-sea-filter` skips the rule. The footer's `determination:details` states which
removal rules ran.

The size rules run after the union because filtering before it deleted fields that a
seam had cut into two halves below the minimum, and a cap applied to merge's pre-union
pixel area let an over-cap union through. The hole fill runs before both size tests, so
a part of a multipart parcel and a standalone parcel of the same shape are judged on
the same post-fill area. Polygonizing the 2.5 m raster leaves half-pixel (3.125 m²)
holes inside ~40% of parcels. 20 m² is ~3 px, so larger holes (buildings, ponds, a
neighbouring field) are kept. Two pieces merge only when they overlap, since a shared
edge means two neighbouring fields and not one field seen twice. All metric work uses
the zone's north UTM CRS so that the halves of a field straddling the equator are
comparable (a per-row hemisphere EPSG put them 10,000 km apart).

Output is `{out}/{year}/zone={NN}/utm{NN}.parquet`, the hive layout that
`catalog/vector/AGENTS.md` documents and that every collection's
`"partition:glob": "./zone=*/utm*.parquet"` and `tools/rebuild_index.py` require. The
staged file is validated before publication, and more rows out than in or any duplicate
parcel id aborts the write. Rows are sorted by Hilbert index. The file carries fiboa
v0.3.0 metadata whose `determination:details` reports the spec, tolerance and cap read
from merge's `_summary.json`, and says INCOMPLETE when the merge ran with
`--allow-missing`. It releases the documented nine-column schema, and the QA fields stay
in the intermediate files. These geometric operations do not guarantee defect-free
coverage.

## Converting large zones in ranges

`fiboa_convert.py` holds a whole zone in one task, which reached 24 GB on the largest
2025 zone (2.2 million rows). `fiboa_ranges.py` splits that work. `plan` runs the seam
union once and hashes the other rows into buckets, `range` puts one bucket into final
form and sorts it, and `stitch` merges the sorted buckets on (Hilbert key, id) and
writes the zone file. Each row's final form depends only on that row, so the output is
byte-identical to `fiboa_convert.py`, with the same rows, order, row groups and footer.

`submit` chains the three phases as Slurm jobs per zone, and converts a zone with fewer
than `--rows-per-range` rows (400,000 by default) in a single job sized by
`fiboa_tiers.py`. `local` runs the same phases on one machine. `submit` passes no
account, so set `SBATCH_ACCOUNT` if your cluster needs one.

```sh
python pipeline/postprocessing/fiboa_ranges.py submit --year 2025 --in-root merged \
    --out-root fiboa --tmp-dir scratch/duckdb --land-polygons land_polygons.parquet \
    --exclude 'gpu-nodes'
```

## Vector QA

`qa_vector.py` checks a converted year. It reports parcels and km² per zone, the size
cap, duplicate ids, parcels in the orphan strips the claim rule fills, fields kept by
two neighbouring zones and overlapping pairs across the equator. With `--land-polygons`
it adds a sea sample, and with `--footprints` it looks for a pile-up or gap of parcels
along each tile's north edge. `--release`, or `--prev-year` with `--prev-root`, compares
the year with a released tree or another year and lists the zones whose parcel count
moved by more than `--zone-tol`.

```sh
python pipeline/postprocessing/qa_vector.py 2025 --root fiboa \
    --out qa/vector_2025.json --release released-fiboa
```

`tools/hash_remote.py --local PREFIX=DIR` computes the `file:size` and `file:checksum`
of zone files before upload, keyed by their published URLs.

## Validation before publication

`validate_vector.py` checks a staged tree before publication. It covers the schema,
ZSTD compression, row-group size, CRS metadata, sampled geometry validity, ids, area
and score, the hive layout and the zone count, and it reports every problem it finds
instead of stopping at the first.

```sh
python pipeline/postprocessing/validate_vector.py --root fiboa 2024 2025 --expect-zones 54
```

`fill_small_holes.py` fills small interior rings in a staged tree that was converted
before the hole rule existed. It moves `metrics:area` and `metrics:perimeter` by the
filled rings and adds the hole clause to the footer. It has been verified to be a no-op
on the 2e release (no published zone file has a ring under 20 m²). It writes nothing
when a file has no small holes, because a rewrite changes the bytes the catalog records
as `file:size` and `file:checksum`. Its docstring lists the four places that must be
regenerated if a patched file is ever published.

```sh
python pipeline/postprocessing/fill_small_holes.py --in-root fiboa --out-root fiboa-filled \
    --index $SLURM_ARRAY_TASK_ID        # one (year, zone) file per array task
```

## Compact raw outlines

The raw outlines of the published 2017-2025 vectors (the output of `outlines.py`,
before simplification, merging and the filters) are kept so that post-processing can be
rerun without redoing BoundaryVote, which is the expensive step. As lon/lat WKB they
take about 486 GB a year. Every vertex sits on the 2.5 m UTM grid, so
`compact_outlines.py` stores each tile as grid integers (start point, ring and part
counts, delta-coded steps) with the attribute columns unchanged, which is 33 GB for
2025 (6.8%). The compact files are on Source Cooperative under
`global-data-2e/intermediate/outlines-compact/{year}/{tile}.parquet`, with `_empty.txt`
(tiles that produced no parcels have no file) and `_manifest.json` (size and sha256 of
every file).

```sh
# expand one tile back to GeoParquet-compatible WKB, with the original geo metadata
python pipeline/postprocessing/compact_outlines.py decode 15SVD_0_0.parquet 15SVD_0_0.wkb.parquet
# compact a year of raw outlines; the compact file is read back and must hold the original
# attributes and the grid integers of the original coordinates before it replaces anything
python pipeline/postprocessing/compact_outlines.py encode --year 2025 \
  --in-root outlines --out-root outlines-compact [--shard 0 --num-shards 8] [--delete-original]
# list of raw tiles + empty markers -> _empty.txt and the manifest; --check re-hashes a copy
python pipeline/postprocessing/compact_verify.py 2025 --raw-list raw.txt --empty-list empty.txt \
  --compact-dir outlines-compact/2025 --manifest manifest.json
```

Decoded coordinates equal the original to float precision but not bit for bit, because
the PROJ inverse can differ in the last bit between machines (3.6e-15 degrees between
the cluster the outlines were made on and the cloud image the first years were
compacted on). For that reason the check compares grid integers and attributes and not
WKB bytes. On two tiles of 2019, decoding the published files with this code reproduced
every attribute column exactly and every geometry to 1e-12 degrees, and compacting the
raw outlines reproduced the published tables.

## Tests

```sh
uv pip install pytest
.venv/bin/python -m pytest pipeline/postprocessing/tests
```

The tests use small synthetic fixtures. A complete outline run needs real score COGs.

## Where the published 2017-2025 vectors differ from this code

The published vectors were made in the 2026-10 rerun by an earlier, unpublished version
of this code. They use the spec, the inland-water rule, the sea rule and the footer
wording described above, and the same constants: row groups of 8,192 rows, zstd 19, the
nine columns, the Hilbert order of the bbox centre, the seam overlap rule (10% of the
smaller piece and 100 m²), the 20 m² hole fill, the 900 m² part and parcel floor and the
5 km² cap after the union. Both join equator seams in the zone's north UTM CRS. The
orphan-strip claims (`ownership.py`, with the run's owned squares in
`owned_squares_2e.csv`) and the cross-zone seam pass (`cross_zone_seams.py`) are also
the same. On 200,000 random parcels near zone edges the claim decisions equal the
run's, and on a synthetic pair of zone files the seam pass writes the same rows and
geometries. This directory differs in five ways.

- The run dropped single-tile pieces under 900 m² before the seam union and judged the
  merged groups again after it, so a field cut into one piece over and one piece under
  900 m² was published as the larger piece alone (0 observed in 5 zones of 2025; at most
  about 2,000 fields over the nine years were expected). Here both floors run after the
  union, so the union is kept.
- The run's `patch_saturation.py` wrote `patch_sat_mean`, `patch_sat_max` and
  `patch_pb_mean` per parcel for `merge_polygons.py --aux-root`. They are not in the
  nine released columns, no rule uses them, and nothing here writes them.
- The run simplified one tile at a time, in shards of 8 threads, with a Rust port of
  GEOS 3.13.1 coverage simplification. `polygons.py` here calls the `coarsen` package,
  which returns the same geometry as one GEOS 3.13.1 `coverage_simplify` pass over the
  same input (802 of 802 parcels of a 2019 tile). The run did not make one pass.
  Polygons that GEOS reports as coverage-invalid (about 1% of parcels, 16 of 1,546 and
  12 of 802 in two 2019 tiles) were simplified on their own with Douglas-Peucker at up
  to 1.2 m, and only the rest went through `coverage_simplify`. That changes the outline
  of 1-2% of parcels, mostly the invalid polygons themselves, and `polygons.py` explains
  why this directory keeps one pass. On those two tiles `simplify_polygons.py` here
  reproduces 96% of the published simplified outlines to within 1e-9°. It also
  refreshes `area_m2` after simplification, so merge's 5 km² cap sees the simplified
  area, while the run capped at merge on the pixel-count area and again after the seam
  union.
- The run converted each zone with a range split that bucketed rows by the Hilbert key
  of the source row and concatenated the buckets' row groups. `fiboa_ranges.py` here
  buckets by hash and merges on the key of the final row, so its output is
  byte-identical to `fiboa_convert.py`.
- The run cropped each window to the blocks that contain foreground before calling
  BoundaryVote, for speed, and this directory calls it on the whole window. The two are
  expected to agree, but that has not been checked.

The sea rule here reads the OSM land-polygons snapshot you prepare. The published
run used the polygons of 2026-10-04 (`land-polygons-split-4326`), so a later
download can differ along coasts.

## Running a year on SLURM

`run_year.sh` submits one year's chain, each stage `afterok` on the one before:
`outlines.sbatch` (an array over the tile list) → `simplify.sbatch` (one array per size class) →
`merge.sbatch` → `fiboa.sbatch`, which runs `fiboa_ranges.py submit` and then submits
`qa_vector.sbatch` after every zone job it started. The zone jobs follow from each zone's merged
row count, so they can only be submitted once merge has finished.

```sh
export SBATCH_ACCOUNT=<account> SBATCH_PARTITION=cpu EXCLUDE='rails[04-15]'
export AWS_ACCESS_KEY_ID=<EODATA key> AWS_SECRET_ACCESS_KEY=<EODATA secret>
OUT=/scratch/ftw/2025 SCORES=/scratch/ftw/raster INDEX_DIR=index TILES=tiles_2025.txt \
  FTW_LAND_POLYGONS=land_polygons.parquet pipeline/postprocessing/run_year.sh 2025
```

`OUT` is the run root, and the stages write `outlines/`, `simplified/`, `merged/`, `fiboa/`,
`qa/vector_{year}.json`, `scratch/` and `logs/` under it. `TILES` lists the year's expected
tiles and is merge's `--keep-list`. `EMPTY` optionally lists tiles without a score COG (no
source mosaic): outlines skips them and merge receives them as `--empty-list`. sbatch reads the
account and partition from `SBATCH_ACCOUNT` and `SBATCH_PARTITION`; `EXCLUDE` (the GPU nodes on
Rails, as above) goes to every job, including the zone jobs. The jobs run
`$REPO/.venv/bin/python` unless `PY` names another interpreter, and they read the environment
`run_year.sh` exports, so set the EODATA keys (see the QA context above) before running it.
`TILES_PER_TASK` (12) and `THROTTLE` (50) set the outline array, `AFTER=<job id>` holds the
first stage until that job succeeds, and `FROM=simplify|merge|fiboa|qa` starts the chain at a
later stage. Every stage resumes on its fingerprints, so after a failed or timed-out task,
running the same command again redoes only the work that is not current.

The sizes below were measured on Rails. Simplification needs about 25 times the outline file's
size in memory, so it runs as three arrays by file size; sizing every task for the largest tile
would reserve 40G for tasks that mostly need 1 GB.

| job | per task | measured |
| --- | --- | --- |
| `outlines.sbatch`, one worker | 2 CPUs, 26G, 8 h | 17.5 GB MaxRSS, ~24 min a tile with `fbp.run_windowed` |
| `simplify.sbatch` < 40 MB, 32 tasks | 1 CPU, 2G | 38 MB file: 0.9 GB, 11 s |
| `simplify.sbatch` 40-200 MB, 16 tasks | 1 CPU, 8G | about 25 times the file size |
| `simplify.sbatch` >= 200 MB, 8 tasks | 1 CPU, 40G | 931 MB file (largest of 2025): 23.5 GB, 3 min |
| `merge.sbatch` | 6 CPUs, 64G, DuckDB 48 GB | published-run merge: 10-24 min, 4-5 cores busy |
| `fiboa.sbatch` (submits only) | 1 CPU, 2G | seconds |
| zone jobs | `fiboa_tiers.py` and `fiboa_ranges.py submit` | see [Converting large zones in ranges](#converting-large-zones-in-ranges) |
| `qa_vector.sbatch` | 4 CPUs, 20G, DuckDB 16 GB | not measured on a full year |
| `compact.sbatch` | 1 CPU, 8G | published run: up to 7.3 GB, 15 min a task at 40 tasks a year |

The merge figures come from the merges of the published 2017-2025 vectors, which had 16 CPUs
and the same 48 GB DuckDB limit. Compaction is not part of the chain. Run it once the year has
merged, and add
`DELETE_ORIGINAL=1` to remove each raw tile after its compact file is verified:

```sh
sbatch --array=0-39 --export=ALL,YEAR=2025,NSHARDS=40,OUT=$OUT pipeline/postprocessing/compact.sbatch
```

Each `.sbatch` file can also be submitted on its own from the repository root (`REPO` defaults to
the submit directory); its header lists the variables it needs.
