# Fields of the World — Global Data (beta)

Beta release of the Fields of the World (FTW) global field-boundary
predictions on [Source Cooperative](https://source.coop/ftw/global-data-beta).

The bucket holds two product families, structured per year so new years drop
in incrementally:

- **Vector** — per-UTM-zone GeoParquet field polygons
  (`vector/{2024,2025}/utm{NN}.parquet`, 54 zones per year, fiboa/vecorel
  schema).
- **Raster** — two-band uint8 field/boundary-probability Cloud-Optimized
  GeoTIFFs at 2.5 m (`raster/{2017..2025}/`).

Predictions derive from the
[TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

This Portolan/STAC catalog is under construction: collections for the vector
and raster trees, and a browsable per-year PMTiles product, land
incrementally. The catalog metadata is maintained in
[fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog);
report problems in its
[issue tracker](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).
