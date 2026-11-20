# FTW Global (2nd Edition) — Vector field boundaries

The 2018, 2020, 2021, 2022, 2023, 2024 and 2025 collections use the updated processing documented in their year collections. Other year links retain historical published collections; they do not imply completion of the updated recipe for those years. Consult each collection for its provenance, footprint and asset inventory.

Per-year collections of predicted agricultural field boundaries as per-UTM-zone GeoParquet. Part of [Fields of the World](https://fieldsofthe.world).

**[Open the interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/)** to explore the fields over imagery, or **[open the catalog in the Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/catalog.json)** to walk the metadata. The files are listed on [Source Cooperative](https://source.coop/ftw/global-data-2e).

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

| Year | Processing | Raster tiles | Predicted parcels |
|---|---|---:|---:|
| [2017](./2017/collection.json) | Historical published collection | See collection | See collection |
| [2018](./2018/collection.json) | Updated | 14,572 | 207,793,034 |
| [2019](./2019/collection.json) | Historical published collection | See collection | See collection |
| [2020](./2020/collection.json) | Updated | 14,584 | 213,576,412 |
| [2021](./2021/collection.json) | Updated | 14,584 | 222,422,107 |
| [2022](./2022/collection.json) | Updated | 14,578 | 219,322,708 |
| [2023](./2023/collection.json) | Updated | 14,590 | 217,529,063 |
| [2024](./2024/collection.json) | Updated | 14,593 | 204,629,903 |
| [2025](./2025/collection.json) | Updated | 14,590 | 225,542,717 |

For 2018, 2020, 2021, 2022, 2023, 2024 and 2025, extraction uses a minimum mean field probability of 0.10, fills enclosed holes strictly smaller than 100 m², and uses a minimum area of 900 m². Independent crop filtering evaluates polygons with finite area from 900 through 5,000,000 m² against the union of native cropland observations from 2017, 2020 and 2024, removing polygons with less than 5% overlap. Unknown or incomplete coverage, nonfinite areas and polygons outside that size interval are retained by this filtering stage. Downstream vector cleanup is documented separately in each vector collection; its 20 m² hole-cleanup description is distinct from extraction hole filling.

Each year is an independent prediction, so a parcel `id` carries no meaning across years; comparing years needs a spatial join.

The pipeline that built these files is documented in [pipeline/README.md](https://github.com/fieldsoftheworld/ftw-global-data-catalog/blob/main/pipeline/README.md), and the catalog itself is maintained at [github.com/fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog).
