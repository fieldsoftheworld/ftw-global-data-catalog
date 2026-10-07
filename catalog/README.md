# Fields of the World — Global Data (2nd Edition)

2nd Edition of the Fields of the World (FTW) global field-boundary
predictions for 2017 to 2025: **1,242,648,258 predicted field polygons** as
cloud-native GeoParquet, and **67,197 field/boundary-probability tiles**
(25.8 TB) as Cloud-Optimized GeoTIFFs. Both derive from the
[TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/),
and both are part of [Fields of the World](https://fieldsofthe.world).

## Three ways in

- **[Interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/):**
  pan to a region, switch years, and compare the predictions with the imagery
  they came from.
- **[Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/catalog.json):**
  walk the STAC tree to see what each year contains.
- **[Source Cooperative](https://source.coop/ftw/global-data-2e):** download
  the files, or read them in place over HTTP range requests from
  `https://data.source.coop/ftw/global-data-2e/`.

Agents: [AGENTS.md](https://data.source.coop/ftw/global-data-2e/AGENTS.md)
beside this file is the agent guide. Read [Limitations](#limitations) before
drawing conclusions from any of these numbers.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

One collection per year in each tree, with both products on the same
footprint.

| Year | Parcels | Tiles (size) | Map |
|---|---|---|---|
| 2017 | 133,102,683 | 7,466 (2.85 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2017) |
| 2018 | 129,911,998 | 7,466 (2.84 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2018) |
| 2019 | 129,384,130 | 7,466 (2.82 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2019) |
| 2020 | 139,322,555 | 7,466 (2.83 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2020) |
| 2021 | 145,101,573 | 7,466 (2.87 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2021) |
| 2022 | 147,088,177 | 7,466 (2.90 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2022) |
| 2023 | 141,468,617 | 7,467 (2.93 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2023) |
| 2024 | 130,526,810 | 7,467 (2.87 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2024) |
| 2025 | 146,741,715 | 7,467 (2.87 TB) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2025) |

The **vector** product is one GeoParquet file per UTM zone per year, at
`vector/{year}/zone=NN/utm{NN}.parquet`, with a PMTiles archive per year for
maps. The [vector README](https://source.coop/ftw/global-data-2e/vector) has a
runnable query.

The **raster** product is one COG per tile at 2.5 m, at
`raster/{year}/…/{tile}.tif`, with field (band 1) and boundary (band 2)
probabilities. All years share one tile grid, so a tile can be compared across
years. Find tiles in the
[raster index](https://data.source.coop/ftw/global-data-2e/index/raster.parquet);
the [raster README](https://source.coop/ftw/global-data-2e/raster) has the
details.

The vectors are polygons derived from the same probabilities the rasters
publish, so treat the two as shapes of one prediction, not as independent
measurements.

## How this was made

The FTW `unet_balanced_fp32.onnx` model ran on the Sentinel-2 quarterly mosaics
(four quarters, 10 m) and produced field and boundary probabilities on a 2.5 m
grid, which are the rasters. Nodata gaps in the mosaics were filled from a
31-pixel window before inference, with TF32 off and no test-time augmentation.
BoundaryVote (`nbg-pb-h0.01-t0.5+R25+F10+G2+A900+q1`) turned the probabilities
into polygons, and parcels over 5 km², parcels that are mostly inland water and
parcels on the sea were removed. The full pipeline is documented in
[pipeline/README.md](https://github.com/fieldsoftheworld/ftw-global-data-catalog/blob/main/pipeline/README.md).

## Limitations

These are **model predictions**, not a survey. A field here is a
*remote-sensing field unit*, a connected component of predicted field-interior
pixels, and **not** a cadastral or legal parcel. This is not a land-tenure
product, and one legal parcel may map to many polygons or to none. Counts,
areas and perimeters are predicted quantities that carry the model's errors.
See [Fields of the World](https://fieldsofthe.world) for the project and its
definitions.

- The `score` column is the model's mean field probability inside the parcel
  (0 to 100), not a calibrated probability, so a score of 80 is not an 80%
  chance that the parcel is real.
- Predictions are weaker outside the training distribution, for example in
  smallholder systems. Prefer the continuous `score` over a hard threshold
  there.
- Coverage is cropland-gated, so it is not global. Only MGRS tiles with at
  least 1% cropland were processed, and an empty region means the pipeline
  never ran there, not that it found no fields.
- Inside a processed tile only the water, sea and size rules above remove
  parcels, so scrub and built-up ground can still carry predicted parcels.
- Where the Sentinel-2 mosaics have large nodata gaps, mainly the sparse
  2017–2019 mosaics, detections are probably under-reported even after the gaps
  were filled. Parcel counts for 2017, 2018 and 2019 are about 9–12% below
  2025, and part of that gap is the input, not the land.
- Each year is an independent prediction. A parcel `id` carries no meaning
  across years, so compare years in the vectors with a spatial join. The
  rasters share one grid and compare per pixel.

## Coordinate systems

Vector GeoParquet is WGS 84 lon/lat (EPSG:4326) in every zone file, so
`ST_Area` on `geometry` returns square degrees; read `metrics:area` (m²)
instead. Each COG is in its own tile's UTM zone, so a mosaic across zones needs
a warp. PMTiles are Web Mercator (EPSG:3857).

## Reading an area across tiles

See the [raster README](https://source.coop/ftw/global-data-2e/raster) for a
short `rasterio` example that mosaics tiles for a lon/lat box.

## Fixing this metadata

`catalog/` in
[fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog)
is this catalog, and the same repository holds the processing pipeline. Report
problems in the
[issue tracker](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).
