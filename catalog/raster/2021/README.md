# FTW Global — Field & Boundary Probabilities 2021 (COG)

Field and boundary probability rasters for 2021: **7,466 Cloud-Optimized GeoTIFFs** at 2.5 m (2.88 TB), one per Sentinel-2 MGRS-based tile. Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Browse it in the [data browser](https://source.coop/ftw/global-data-beta).

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## The rasters

Each COG is 40,032 × 40,032 pixels at 2.5 m in its tile's UTM zone, with two uint8 bands scaled by 1/255: `field` (band 1, field-interior probability) and `boundary` (band 2, field-boundary probability). No nodata value is declared, so every pixel carries a probability. ZSTD-compressed COG layout with average-resampled overviews down to 626 px. Produced by the `unet_balanced_fp32` FTW model from 16 input bands (B02/B03/B04/B08 × quarters Q1–Q4 of the year's [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/), 10 m); each COG's GDAL metadata records its four source mosaic tiles (`source_items`).

| # | Band | Type | Scale | Resolution | Meaning |
|---|---|---|---|---|---|
| 1 | `field` | uint8 | 1/255 | 2.5 m | Field-interior probability: the model's probability that the pixel lies inside an agricultural field. probability = value × 1/255. |
| 2 | `boundary` | uint8 | 1/255 | 2.5 m | Field-boundary probability: the model's probability that the pixel lies on a field boundary. probability = value × 1/255. |

Each tile will get its own directory (`raster/2021/{tile}/`) holding the COG, its STAC item and its thumbnail. The copy into those keys is still running, so the COGs currently answer at `raster/2021/{tile}.tif` (for example `01KFS_0_0.tif`).

## Find tiles

The [index manifest](https://data.source.coop/ftw/global-data-beta/index/raster.parquet) lists every tile with href, size, bbox, `epsg`, and per-tile `field_frac`/`boundary_frac`/`cropland_frac` pixel fractions. The five field-densest tiles of 2021:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
idx = "https://data.source.coop/ftw/global-data-beta/index/raster.parquet"
con.sql(f"""
    SELECT tile_key, epsg, round(field_frac, 3) AS field_frac
    FROM read_parquet('{idx}') WHERE year = 2021
    ORDER BY field_frac DESC LIMIT 5
""").show()
```

Summarising the whole 2021 collection from the same manifest (no raster reads):

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
idx = "https://data.source.coop/ftw/global-data-beta/index/raster.parquet"
con.sql(f"""
    SELECT count(*) AS tiles,
           count(DISTINCT epsg) AS utm_crs,
           round(sum(size_bytes) / 1e12, 2) AS tb,
           round(avg(field_frac), 4) AS mean_field_frac,
           round(max(field_frac), 4) AS max_field_frac,
           -- 1e10 m2 == 1 Mha; assumes tiles do not overlap
           round(sum(field_frac) * 40032 * 40032 * 6.25 / 1e10, 1)
               AS field_mha_approx
    FROM read_parquet('{idx}') WHERE year = 2021
""").show()
```

## Read a tile

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2021/01KFS_0_0.tif
```

Any COG reader works over HTTP range requests; the overviews make low-zoom reads cheap.
