[![Taylor Geospatial](https://data.source.coop/ftw/global-data-2e/assets/readme/tg-logo.png)](https://taylorgeospatial.org)

# Fields of the World — Global Data (2nd Edition)

The 2018, 2020, 2021, 2022, 2023, 2024 and 2025 collections use the updated processing documented in their year collections. Other year links retain historical published collections; they do not imply completion of the updated recipe for those years. Consult each collection for its provenance, footprint and asset inventory.

Fields of the World global field-boundary predictions, organized by year as GeoParquet vectors and probability COGs.
Both derive from the [TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/), and both are part of [Fields of the World](https://fieldsofthe.world).

## Examples

These historical illustrations predate the updated 2018/2020/2021/2022/2023/2024/2025 processing; they are not validation of the updated outputs.

### 1st and 2nd edition

**Sorriso, Brazil, 2025 Q3.** Large fields, same imagery in both panels.

| 1st edition | 2nd edition |
|---|---|
| ![Sorriso, Brazil, 1st edition field outlines on 2025 Q3 imagery](https://data.source.coop/ftw/global-data-2e/assets/readme/edition-sorriso-1st.jpg) | ![Sorriso, Brazil, 2nd edition field outlines on the same 2025 Q3 imagery](https://data.source.coop/ftw/global-data-2e/assets/readme/edition-sorriso-2nd.jpg) |

**Normandy, France, 2025 Q3.** Irregular fields and hedgerows, same imagery in both panels.

| 1st edition | 2nd edition |
|---|---|
| ![Normandy, France, 1st edition field outlines on 2025 Q3 imagery](https://data.source.coop/ftw/global-data-2e/assets/readme/edition-normandy-1st.jpg) | ![Normandy, France, 2nd edition field outlines on the same 2025 Q3 imagery](https://data.source.coop/ftw/global-data-2e/assets/readme/edition-normandy-2nd.jpg) |

### 2018 and 2025

**Matopiba, Brazil.** New rectangular field blocks.

| 2018 Q3 | 2025 Q3 |
|---|---|
| ![Matopiba, Brazil, 2018 Q3 imagery with 2018 field outlines](https://data.source.coop/ftw/global-data-2e/assets/readme/change-matopiba-2018.jpg) | ![Matopiba, Brazil, 2025 Q3 imagery with 2025 field outlines](https://data.source.coop/ftw/global-data-2e/assets/readme/change-matopiba-2025.jpg) |

**West Bahia, Brazil.** Rectangular fields replaced by center pivots.

| 2018 Q1 | 2025 Q1 |
|---|---|
| ![West Bahia, Brazil, 2018 Q1 imagery with 2018 field outlines](https://data.source.coop/ftw/global-data-2e/assets/readme/change-west-bahia-2018.jpg) | ![West Bahia, Brazil, 2025 Q1 imagery with 2025 field outlines](https://data.source.coop/ftw/global-data-2e/assets/readme/change-west-bahia-2025.jpg) |

Imagery is Copernicus Sentinel data 2017–2025 from the TGE Labs quarterly mosaics, outlines are FTW predictions, and these are selected examples, not an accuracy assessment.

## Three ways in

- The [interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/) lets you pan to a region, switch years, and compare the predictions with the imagery they came from.
- The [Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/catalog.json) walks the STAC tree and shows what each year contains.
- [Source Cooperative](https://source.coop/ftw/global-data-2e) hosts the files for download, and you can also read them in place over HTTP range requests from `https://data.source.coop/ftw/global-data-2e/`.

[AGENTS.md](https://data.source.coop/ftw/global-data-2e/AGENTS.md) beside this file is the guide for agents.
Read [Limitations](#limitations) before drawing conclusions from any of these numbers.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

| Year | Processing | Raster tiles | Predicted parcels |
|---|---|---:|---:|
| [2017](./vector/2017/collection.json) | Historical published collection | See collection | See collection |
| [2018](./vector/2018/collection.json) | Updated | 14,572 | 207,793,034 |
| [2019](./vector/2019/collection.json) | Historical published collection | See collection | See collection |
| [2020](./vector/2020/collection.json) | Updated | 14,584 | 213,576,412 |
| [2021](./vector/2021/collection.json) | Updated | 14,584 | 222,422,107 |
| [2022](./vector/2022/collection.json) | Updated | 14,578 | 219,322,708 |
| [2023](./vector/2023/collection.json) | Updated | 14,590 | 217,529,063 |
| [2024](./vector/2024/collection.json) | Updated | 14,593 | 204,629,903 |
| [2025](./vector/2025/collection.json) | Updated | 14,590 | 225,542,717 |

The **vector** product is one GeoParquet file per UTM zone per year, at `vector/{year}/zone=NN/utm{NN}.parquet`, with a PMTiles archive per year for maps.
The [vector README](https://source.coop/ftw/global-data-2e/vector) has a runnable query.

The **raster** product is one COG per tile at 2.5 m, at `raster/{year}/…/{tile}.tif`, with field (band 1) and boundary (band 2) probabilities.
All years share one tile grid, so a tile can be compared across years.
Find tiles in the [raster index](https://data.source.coop/ftw/global-data-2e/index/raster.parquet).
The [raster README](https://source.coop/ftw/global-data-2e/raster) has the details.

The vectors are polygons derived from the same probabilities as the rasters, so the two are not independent measurements.

## How this was made

For 2018, 2020, 2021, 2022, 2023, 2024 and 2025, the observed model is `ftw-q-unet-efb3.onnx`, SHA256 `d14285f91870a79833aac3529650f385afec605ed415acdf2227a02a0a88d036`. The four Sentinel-2 quarterly mosaics supply the inputs; each raster item records its exact quarter IDs and model provenance.

For 2018, 2020, 2021, 2022, 2023, 2024 and 2025, extraction uses a minimum mean field probability of 0.10, fills enclosed holes strictly smaller than 100 m², and uses a minimum area of 900 m². Independent crop filtering evaluates polygons with finite area from 900 through 5,000,000 m² against the union of native cropland observations from 2017, 2020 and 2024, removing polygons with less than 5% overlap. Unknown or incomplete coverage, nonfinite areas and polygons outside that size interval are retained by this filtering stage. Downstream vector cleanup is documented separately in each vector collection; its 20 m² hole-cleanup description is distinct from extraction hole filling.

## Limitations

These are **model predictions**, not a survey.
A field here is a *remote-sensing field unit*, a connected component of predicted field-interior pixels, and **not** a cadastral or legal parcel.
This is not a land-tenure product, and one legal parcel may map to many polygons or to none.
Counts, areas and perimeters are predicted quantities that carry the model's errors.
See [Fields of the World](https://fieldsofthe.world) for the project and its definitions.

- The `score` column is the model's mean field probability inside the parcel (0 to 100), not a calibrated probability, so a score of 80 is not an 80% chance that the parcel is real.
- Predictions are weaker outside the training distribution, for example in smallholder systems.
  Prefer the continuous `score` over a hard threshold there.
- Coverage differs by year and processing version. Check the actual raster inventory before interpreting an empty region; absence of published polygons does not establish absence of fields.
- The updated crop-overlap filter is described above. Its retained polygons can still include prediction errors; unknown coverage is retained, not treated as zero cropland.
- Where the Sentinel-2 mosaics have large nodata gaps, mainly the sparse 2017–2019 mosaics, detections are probably under-reported even after the gaps were filled.
  Differences in imagery coverage and processing versions can affect cross-year comparisons.
- Each year is an independent prediction.
  A parcel `id` carries no meaning across years, so compare years in the vectors with a spatial join.
  The rasters share one grid and compare per pixel.

## Coordinate systems

Vector GeoParquet is WGS 84 lon/lat (EPSG:4326) in every zone file, so `ST_Area` on `geometry` returns square degrees, so read `metrics:area` (m²) instead.
Each COG is in its own tile's UTM zone, so a mosaic across zones needs a warp.
PMTiles are Web Mercator (EPSG:3857).

## Reading an area across tiles

See the [raster README](https://source.coop/ftw/global-data-2e/raster) for a short `rasterio` example that mosaics tiles for a lon/lat box.

## Fixing this metadata

`catalog/` in [fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog) is this catalog, and the same repository holds the processing pipeline.
Report problems in the [issue tracker](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).
