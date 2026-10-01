# Polygon postprocessing

Probability COGs → windowed parcel outlines → coverage simplification →
centroid ownership / size filtering → seam repair and fiboa GeoParquet.
The existing PMTiles pipeline consumes the released zone files.

```sh
uv venv
uv pip install -r pipeline/postprocessing/requirements.txt
# Install the forthcoming fbp package separately; it is not on PyPI yet.
```

`outlines.py` calls `fbp.methods.parse`; no BoundaryVote implementation is
vendored. The private package must expose the production method
`nbg-pb-h0.01-t0.3+R35+F10+G2+A900`. The default `exact` backend uses this ID;
`--backend fast` adds `+q1` and requires that variant in fbp. Until the package
is released, the outline stage requires separately authorized package access.

```sh
.venv/bin/python pipeline/postprocessing/outlines.py --year 2025 \
  --scores scores --out-root outlines --index-dir index --workers 1
.venv/bin/python pipeline/postprocessing/simplify_polygons.py --year 2025 \
  --in-root outlines --out-root simplified --workers 1
.venv/bin/python pipeline/postprocessing/merge_polygons.py --year 2025 \
  --in-root simplified --out-root merged --no-aux \
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

Merge retains `in_utm_zone AND in_mgrs_square`, dropping parcels >5 km².
`tiles.txt` lists expected tiles; `empty.txt` lists verified empty/excluded tiles.
Missing inputs fail unless `--allow-missing` is explicit. Optional patch QA joins
require `--aux-root`; use `--no-aux` when no auxiliary parcel table exists.
Empty outline tiles write readable empty Parquet files.

Conversion repairs and joins seams, drops polygon parts below 900 m², calculates
area/perimeter in UTM, sorts by Hilbert index, and writes fiboa v0.3.0 metadata.
It releases the documented nine-column schema. QA fields remain in intermediate
files. These geometric operations do not guarantee defect-free coverage.

```sh
uv pip install pytest
.venv/bin/python -m pytest pipeline/postprocessing/tests
```

The default exact path avoids the private fast-crop helper. Production fast-path
parity and a complete fbp run require the unreleased package and score fixtures.
