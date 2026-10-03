# FTW Global — Field & Boundary Probabilities 2024 (COG)

Field and boundary probability rasters for 2024: **7,467 Cloud-Optimized GeoTIFFs** at 2.5 m (2.90 TB), one per Sentinel-2 MGRS-based tile. Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

**[Open 2024 on the interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2024)** to see the predictions over imagery, or **[open it in the Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2024/collection.json)** to walk the metadata. The files are listed on [Source Cooperative](https://source.coop/ftw/global-data-2e).

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## The rasters

Each COG is 40,032 × 40,032 pixels at 2.5 m in its tile's UTM zone, with two uint8 bands scaled by 1/255: `field` (band 1, field-interior probability) and `boundary` (band 2, field-boundary probability). ZSTD-compressed COG layout with average-resampled overviews down to 626 px. Produced by the `unet_balanced_fp32` FTW model from 16 input bands (B02/B03/B04/B08 × quarters Q1–Q4 of the year's [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/), 10 m); each COG's GDAL metadata records its four source mosaic tiles (`source_items`).

## Find tiles

The [index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet) lists every tile with href, size, bbox, and per-tile `field_frac`/`boundary_frac`/`cropland_frac` pixel fractions. The five field-densest tiles of 2024:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
idx = "https://data.source.coop/ftw/global-data-2e/index/raster.parquet"
con.sql(f"""
    SELECT tile_key, href, round(field_frac, 3) AS field_frac
    FROM read_parquet('{idx}') WHERE year = 2024
    ORDER BY field_frac DESC LIMIT 5
""").show()
```

## Read a tile

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-2e/raster/2024/01KFS_0_0.tif
```

Any COG reader works over HTTP range requests; the overviews make low-zoom reads cheap.
