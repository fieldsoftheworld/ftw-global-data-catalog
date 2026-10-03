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
vendored. The private package must expose the production method
`nbg-pb-h0.01-t0.3+R35+F10+G2+A900`. The default `exact` backend uses this ID;
`--backend fast` adds `+q1` and requires that variant in fbp. Until the package
is released, the outline stage requires separately authorized package access —
it fails with one message naming that need rather than once per tile. Only
`outlines.py` needs fbp; the other three stages run without it.

```sh
.venv/bin/python pipeline/postprocessing/outlines.py --year 2025 \
  --scores scores --out-root outlines --index-dir index --workers 1
.venv/bin/python pipeline/postprocessing/simplify_polygons.py --year 2025 \
  --in-root outlines --out-root simplified --workers 1
.venv/bin/python pipeline/postprocessing/merge_polygons.py --year 2025 \
  --in-root simplified --out-root merged --no-aux --tmp-dir scratch/duckdb \
  --keep-list tiles.txt --empty-list empty.txt
.venv/bin/python pipeline/postprocessing/fiboa_convert.py --year 2025 \
  --in-root merged --out-root fiboa --tmp-dir scratch/duckdb --zone 15
```

Install DuckDB's spatial extension once (`INSTALL spatial`) before running
conversion offline. Score inputs are `{scores}/{year}/{tile}.tif`: two uint8
bands (field/boundary), probabilities /255, north-up UTM at 2.5 m.
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
Ownership uses the tile raster's own bounds for its MGRS square and the MGRS
longitude bands, including the 31V/32V exception the Sentinel-2 grid follows.
Simplification uses 5 m in UTM over the **whole** coverage in one pass, so shared
edges stay shared; results are repaired, never re-simplified per geometry, and
attributes are preserved. The Rust implementation is provided by the `coarsen`
PyPI package and this repo calls its Python API.

Merge retains `in_utm_zone AND in_mgrs_square`, dropping parcels >5 km² by
`area_m2`, which simplification refreshes so the cap and the summary totals
describe the geometry actually written. `tiles.txt` lists expected tiles;
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

Conversion repairs geometry, joins seams, then — on the unioned geometry — drops
parts and parcels below 900 m², re-applies merge's km² cap, computes
area/perimeter, and derives the bbox covering. The order matters: filtering before
the union deleted fields cut by a seam into two sub-minimum halves, and a cap
applied to merge's pre-union pixel area let a union over the cap through. Two
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

```sh
uv pip install pytest
.venv/bin/python -m pytest pipeline/postprocessing/tests
```

The default exact path avoids the private fast-crop helper. Production fast-path
parity and a complete fbp run require the unreleased package and score fixtures.
