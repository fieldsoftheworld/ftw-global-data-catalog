# Polygon postprocessing

Probability COGs → windowed parcel outlines → coverage simplification →
centroid ownership / size filtering → seam repair and fiboa GeoParquet.
The existing PMTiles pipeline consumes the released zone files.

```sh
uv venv
uv pip install -r pipeline/postprocessing/requirements.txt
# Install the private fbp package separately. It is NOT on PyPI, and it is NOT
# the unrelated `fbp` 1.3.6 published there, which does not expose fbp.methods.
```

`outlines.py` calls `fbp.methods.parse`; no BoundaryVote implementation is
vendored. It runs the method of the published 2017-2025 vectors,
`nbg-pb-h0.01-t0.5+R25+F10+G2+A900`: non-background pixels as foreground,
watershed seeds from the boundary surface at depth 0.01, a watershed line kept
when half of its pixels have p(boundary) >= p(field) (`t0.5`), regions with mean
p(field) under 25% dropped (`R25`), parcels of the p(field) > 10% mask that the
result covers less than half re-adopted (`F10`) unless under 2% of that mask is
uncovered (`G2`), and parcels under 900 m² dropped (`A900`). The default
`--backend fast` appends `+q1`, an integer h-maxima and bucket-queue watershed on
the 1/255 grid the scores are stored on, which is what the published run used and
what its footers record; `--backend exact` is scikit-image. The method id is part
of each tile's resume fingerprint, so an older outline tile is recomputed.

fbp is public at `fieldsoftheworld/fbp`, but its `main` parses only the `l`, `b`,
`f`, `m` and `A` modifiers: it rejects `+R25`, `+F10`, `+G2` and `+q1`, and has no
`+q1` backend. The published run used a revision of fbp that has not been pushed
there yet, so until it is, `outlines.py` needs that revision installed and fails
with one message naming the requirement rather than once per tile. Only
`outlines.py` needs fbp; the other stages run without it.

```sh
.venv/bin/python pipeline/postprocessing/outlines.py --year 2025 \
  --scores staging-data/raster --out-root outlines --index-dir index --workers 1
.venv/bin/python pipeline/postprocessing/simplify_polygons.py --year 2025 \
  --in-root outlines --out-root simplified --workers 1
.venv/bin/python pipeline/postprocessing/merge_polygons.py --year 2025 \
  --in-root simplified --out-root merged --no-aux --tmp-dir scratch/duckdb \
  --keep-list tiles.txt --empty-list empty.txt
# once: OSM land polygons for the sea rule (osmdata.openstreetmap.de, ODbL)
curl -LO https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip
unzip land-polygons-split-4326.zip
.venv/bin/python pipeline/postprocessing/sea_filter.py \
  land-polygons-split-4326/land_polygons.shp land_polygons.parquet
.venv/bin/python pipeline/postprocessing/fiboa_convert.py --year 2025 \
  --in-root merged --out-root fiboa --tmp-dir scratch/duckdb --zone 15 \
  --land-polygons land_polygons.parquet
```

Install DuckDB's spatial extension once (`INSTALL spatial`) before running
conversion offline. Score inputs are `{scores}/{year}/{tile}/{tile}.tif` or the
grouped `{scores}/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif` (the published
hierarchy in either of the layouts inference writes — both are discovered, so a
half-migrated tree needs no flag): two uint8 bands (field/boundary),
probabilities /255, north-up UTM at 2.5 m.
QA context requires `index/tile_index_{year}*.parquet` with `tile_key`, `quarter`,
`b04_s3_href` and `b04_s3_endpoint`: four source mosaic B04 objects per tile. The
mosaic downloader can emit this index with `--index-output`. `b04_s3_href` is an
`s3://bucket/key` on CDSE EODATA, opened as `/vsis3/bucket/key` against the
endpoint the row records, so set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
to the EODATA keys first. The index's `b04_odata_href` is OIDC-authenticated,
cannot be opened by GDAL, and is carried for provenance only; these assets have
no public unauthenticated https form, so there is no fallback.

Terrain context reads public Copernicus DEM GLO-30 and IO annual land cover.
The land-cover vintages follow the product year (`context.LULC_BY_YEAR`): the
water mask uses the newest sampled vintage and `frac_crops_ever` is cropland in
any sampled year, so a 2020 product is never scored against 2024 land cover. An
unlisted year fails rather than silently borrowing another year's vintages.

Core/halo defaults are 8192/512 pixels. Core centroid ownership reduces window
duplicates, but parcels wider than the halo may be truncated or duplicated;
`touches_window_edge` identifies candidates and conversion joins seam parcels.
Outline tiles resume on a fingerprint covering the source COG's identity, the
flags **and** `OWNERSHIP_RULES`, so changing which parcels a tile claims rewrites
every tile on the next run rather than resuming onto the previous rule's parquet;
bump that constant with every `in_utm_zone`/`in_mgrs_square` change.
Ownership uses the tile raster's own bounds for its MGRS square and the MGRS
longitude bands, including the 31V/32V exception the Sentinel-2 grid follows.
The square is the 100 km cell the raster's north-west corner snaps to, so for a
stacked `_r_c` sub-tile it is not the cell the tile key names (59GQQ_0_1 owns the
59GQP cell) — the square follows the pixels. Two consequences are known and
measured: stacked sub-tiles claim abutting squares whose shared edge sits 80 m
north of where their rasters abut, leaving an 80 m × 100 km strip (~8 km², nine
items) owned by neither; and because neighbouring squares abut exactly, the
cross-tile seam union is load-bearing for parcels on a tile boundary. Such a
parcel is truncated at each raster's own data edge — which `touches_window_edge`
does not flag, as it marks only window borders interior to the raster — and each
half is centred in its own square, so both are owned. Same-zone rasters overlap
by only 40–120 m while `MIN_OVERLAP` wants 10% of the smaller half, so a field
reaching more than ~1.2 km each side of a same-zone seam is published as two
overlapping halves; `test_a_wide_field_across_a_same_zone_seam_is_not_rejoined`
records it, and loosening the cross-tile branch for a shared tile boundary should
land before the next generation runs.
Simplification uses 5 m in UTM over the **whole** coverage in one pass, so shared
edges stay shared; results are repaired, never re-simplified per geometry, and
attributes are preserved. The Rust implementation is provided by the `coarsen`
PyPI package and this repo calls its Python API.

Merge retains `in_utm_zone AND in_mgrs_square AND area_m² <= 5 km² AND
frac_water < 0.7`. `area_m2` is the value simplification refreshes, so the cap
and the summary totals describe the geometry actually written. `frac_water` is
the share of a parcel's pixels that are class water in the Impact Observatory
io-lulc **2024** layer, for every product year (`context.WATER_VINTAGE`), so a
reservoir that filled or drained between 2017 and 2025 is judged by its 2024
state; it removes inland water, aquaculture ponds and salt pans (2025: 1.91M
parcels at 0.7, 2.01M at 0.5). 0.7 and not 0.5 because rice paddies are flooded
in one quarter and vegetated in another: in 20-chip samples on false-colour
Sentinel-2, [0.5, 0.7) held 9 crop fields to 6 ponds and [0.7, 0.9) held 2 fields
to 12 ponds. `--max-frac-water 2` keeps all of it, and the value is in the merge
fingerprint and `_summary.json`. `tiles.txt` lists expected tiles;
`empty.txt` lists verified empty/excluded tiles. The two are disjoint, and an
input tile in neither is refused. Missing inputs fail unless `--allow-missing` is
explicit; with it, `_summary.json` records `allow_missing`, the missing list and
the expected/present counts, and `fiboa_convert` stamps "INCOMPLETE" into
`determination:details` so an incomplete release is identifiable.

Optional patch QA joins require `--aux-root`; use `--no-aux` when no auxiliary
parcel table exists. Each tile's aux file is matched exactly (`{aux}/{tile}.parquet`,
never a zone prefix glob) and checked for uniqueness on `(tile_key, parcel_id)`
before the join; a zone with no aux file joins NULL rather than failing, so every
zone file in a year carries the same schema. The written row count is compared
with the pre-join retained count, so a fan-out cannot reach the release.

Zones resume on a `merge_fingerprint` covering the input files' identity and the
filter, so regenerated inputs and changed flags are rewritten rather than skipped;
`--force` rewrites regardless. A zone that retains nothing on a rerun has its
partition removed, so no stale generation survives into `fiboa_convert`'s zone
discovery. DuckDB spills to `--tmp-dir` (pid-scoped, outside `--out-root`), and
the per-zone temp is `part-0.parquet.tmp-<pid>`, which no `*.parquet` glob matches.
Empty outline tiles write readable empty Parquet files.

Conversion repairs geometry, joins seams, then — on the unioned geometry — fills
interior rings under 20 m², drops parts and parcels below 900 m², re-applies
merge's km² cap, computes area/perimeter, and derives the bbox covering. The sea
rule (`sea_filter.py`) then drops parcels with less than half their area on OSM
land polygons, because io-lulc has no data over open sea and counts nodata as dry
(south-west Norway 2025: 3,361 offshore parcels with a median `frac_water` of 0).
The OSM polygons are coastline-derived, so islands, polders and lakes are land,
tidal flats outside the coastline and the Caspian are sea. `--land-polygons` (or
`$FTW_LAND_POLYGONS`) names the prepared GeoParquet and `--no-sea-filter` skips
the rule; the footer's `determination:details` states which removal rules ran. The order
matters: filtering before
the union deleted fields cut by a seam into two sub-minimum halves, and a cap
applied to merge's pre-union pixel area let a union over the cap through. The hole
fill precedes both size tests, so a part of a multipart parcel and a standalone
parcel of the same shape are judged on the same post-fill area. Polygonizing the
2.5 m raster leaves half-pixel (3.125 m²) holes inside ~40% of parcels; 20 m² is
~3 px, and larger holes (buildings, ponds, a neighbouring field) are kept. Two
pieces merge only when they genuinely overlap; a shared edge is adjacency, not
duplication. All metric work uses the zone's north UTM CRS so an
equator-straddling field's halves are comparable — a per-row hemisphere EPSG put
them 10,000 km apart.

Output is `{out}/{year}/zone={NN}/utm{NN}.parquet`, the hive layout
`catalog/vector/AGENTS.md` documents and every collection's
`"partition:glob": "./zone=*/utm*.parquet"` and `tools/rebuild_index.py` require.
The staged file is validated before publication: more rows out than in, or any
duplicate parcel id, aborts rather than publishing. Rows are sorted by Hilbert
index and the file carries fiboa v0.3.0 metadata whose `determination:details`
reports the real spec, tolerance and cap read from merge's `_summary.json`, and
says INCOMPLETE when the merge ran with `--allow-missing`. It releases the
documented nine-column schema; QA fields remain in intermediate files. These
geometric operations do not guarantee defect-free coverage.

Two scripts stand beside conversion rather than in it. `validate_vector.py` checks
a staged tree before publication — schema, ZSTD, row-group size, CRS metadata,
sampled geometry validity, ids, area and score, hive layout and zone count — and
reports every problem it finds rather than stopping at the first:

```sh
python validate_vector.py --root fiboa 2024 2025 --expect-zones 54
```

`fill_small_holes.py` is the contingency for a staged tree converted before the
hole rule existed: it fills the small rings, moves `metrics:area` /
`metrics:perimeter` by the filled rings, and stamps the hole clause into the
footer. It is **verified a no-op on the 2e release** (no published zone file has a
ring under 20 m²), and it writes nothing when a file has no small holes, because a
rewrite changes the bytes the catalog commits as `file:size` and `file:checksum`.
Its docstring lists the four places that must be regenerated if a patched file is
ever published.

```sh
python fill_small_holes.py --in-root fiboa --out-root fiboa-filled \
    --index $SLURM_ARRAY_TASK_ID        # one (year, zone) file per array task
```

```sh
uv pip install pytest
.venv/bin/python -m pytest pipeline/postprocessing/tests
```

The published run cropped each window to the blocks that contain foreground before
calling BoundaryVote, for speed; this directory calls it on the whole window. The
two are expected to agree, and that has not been checked here, because both need
the unpublished fbp revision. A complete outline run needs it and real score
fixtures.

## Where the published 2017-2025 vectors differ from this code

The vectors were made by the production repository (`global-ftw-2e`, commit
`5d01d4b`, not public), not by this directory. The spec, the inland-water rule,
the sea rule and the footer wording above are the same rules. Row groups of 8,192
rows, zstd 19, the nine columns, the Hilbert order of the bbox centre, the seam
overlap rule (10% of the smaller piece and 100 m²), the 20 m² hole fill, the 900 m²
part and parcel floor and the 5 km² cap after the union are the same constants.
What this directory does not do:

- **Orphan-strip claims.** Near some zone edges the mosaic product has no tile for
  the partial 100 km squares (for example zone 32 between 6°E and its 32UL*
  squares at 52-53°N, and zone 16 west of 89.4°W at 42-43°N), so the zone that
  contains such a location has no tile there while the neighbouring zone's tile,
  whose square reaches across the zone edge, flags those parcels as outside its
  zone. Before the rerun nobody
  kept them: 3.0-3.4M parcels and 167-174k km² a year in 2020-2025. The run's tile
  also kept (`claimed`) a parcel of its own square outside its zone when the
  centroid, or any point on the centroid row across the parcel sampled every
  200 m, lies in an adjacent zone's square without a keep-list tile; of two
  claimers the western zone wins. Here `in_utm_zone` is the plain zone test.
- **Cross-zone seams.** A claimed field that reaches into the next zone's first
  square is also cut at the raster edge by the tile there. The run's merge ended
  with a pass that unions each claimed parcel with the overlapping pieces in the
  two neighbouring zone files (same overlap rule), keeping the smallest claimed id
  of the group and removing the other pieces from their zone files. Here such
  pieces stay in both zone files.
- **Piece floor before the union.** The run dropped single-tile pieces under
  900 m² before the seam union and again judged merged groups after it, so a field
  cut into a piece over and a piece under 900 m² was published as the larger piece
  alone (0 observed in 5 zones of 2025; at most about 2,000 fields over the nine
  years were expected). Here both floors run after the union, so the union is kept.
- **Patch statistics.** The run's `patch_saturation.py` wrote `patch_sat_mean`,
  `patch_sat_max` and `patch_pb_mean` per parcel for `merge_polygons.py --aux-root`.
  They are not in the released nine columns and no rule uses them; nothing here
  writes them.
- **Simplification.** The run simplified with a Rust port of GEOS 3.13.1 coverage
  simplification kept in the production repository, one tile at a time in shards of
  8 threads. `polygons.py` here calls the `coarsen` package, which returns the same
  geometry as one GEOS 3.13.1 `coverage_simplify` pass over the same input (802 of
  802 parcels of a 2019 tile). The run did not make one pass: polygons GEOS reports
  as coverage-invalid (about 1% of parcels, 16 of 1,546 and 12 of 802 in two 2019
  tiles) were simplified on their own with Douglas-Peucker at up to 1.2 m, and only
  the rest went through `coverage_simplify`. That changes the outline of 1-2% of
  parcels, mostly the invalid polygons themselves, and `polygons.py` explains why
  this directory keeps one pass. On those two tiles `simplify_polygons.py` here
  reproduces 96% of the published simplified outlines to within 1e-9°. It also
  refreshes `area_m2` after simplification, so merge's 5 km² cap sees the simplified
  area; the run capped at merge on the pixel-count area and again after the seam
  union.
- **Conversion mechanics.** The run converted each zone with `fiboa_ranges.py`,
  which splits a zone's Hilbert key space into ranges of about 400,000 rows to
  bound memory per task and stitches the row groups. It produces the same rows in
  the same order as `fiboa_convert.py`.
- **Equator seams** are joined in the zone's north UTM CRS in both.

The sea rule here reads the OSM land-polygons snapshot you prepare. The published
run used the polygons of 2026-10-04 (`land-polygons-split-4326`), so a later
download can differ along coasts.
