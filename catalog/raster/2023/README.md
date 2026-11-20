# FTW raster probabilities 2023

Field and boundary probability rasters for 2023: 14,590 COGs at 2.5 m, totaling 5,869,183,300,574 bytes. Each raster has two uint8 bands, field and boundary; probability equals pixel value / 255. Follow the 60 UTM-zone catalogs and their grid-zone catalogs to every item. Each item records its actual model checksum, four source mosaic IDs, projection, bounds and data destination. Field and boundary fractions are descriptive coarsest-overview measurements using uint8 >128 and >64; these are not vector extraction thresholds. Crop coverage uses the fixed 2017/2020/2024 observations; 15 unknown fractions remain null. The STAC GeoParquet mirror contains the same item metadata for bulk queries.

[Collection](https://data.source.coop/ftw/global-data-2e/raster/2023/collection.json) lists the browse tree and its checksummed STAC mirror. Item JSON is normative.

Data licence: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
