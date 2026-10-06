# FTW Global (2nd Edition) — Field & boundary probability rasters

Per-year collections of 2.5 m field/boundary probability COGs, 2017–2025: **67,197 tiles**. Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Browse it in the [data browser](https://source.coop/ftw/global-data-2e).

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

- [2017](./2017/collection.json) — 7,466 tiles, 2.95 TB
- [2018](./2018/collection.json) — 7,466 tiles, 2.84 TB
- [2019](./2019/collection.json) — 7,466 tiles, 2.84 TB
- [2020](./2020/collection.json) — 7,466 tiles, 2.85 TB
- [2021](./2021/collection.json) — 7,466 tiles, 2.88 TB
- [2022](./2022/collection.json) — 7,466 tiles, 2.91 TB
- [2023](./2023/collection.json) — 7,467 tiles, 2.94 TB
- [2024](./2024/collection.json) — 7,467 tiles, 2.90 TB
- [2025](./2025/collection.json) — 7,467 tiles, 2.88 TB

## The rasters

Each COG is 40,032 × 40,032 pixels at 2.5 m in its tile's UTM zone, with two uint8 bands scaled by 1/255: `field` (band 1, field-interior probability) and `boundary` (band 2, field-boundary probability). No nodata value is declared, so every pixel carries a probability. ZSTD-compressed COG layout with average-resampled overviews down to 626 px. Produced by the `unet_balanced_fp32` FTW model from 16 input bands (B02/B03/B04/B08 × quarters Q1–Q4 of the year's [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/), 10 m); each COG's GDAL metadata records its four source mosaic tiles (`source_items`).

| # | Band | Type | Scale | Resolution | Meaning |
|---|---|---|---|---|---|
| 1 | `field` | uint8 | 1/255 | 2.5 m | Field-interior probability: the model's probability that the pixel lies inside an agricultural field. probability = value × 1/255. |
| 2 | `boundary` | uint8 | 1/255 | 2.5 m | Field-boundary probability: the model's probability that the pixel lies on a field boundary. probability = value × 1/255. |

All years share the tile grid, so a tile key names the same ground in every year and per-pixel year-over-year comparison works tile by tile.

## Browsing

Each year's tiles are grouped by UTM zone and grid zone designator, read straight off the tile key, and each tile's directory holds its COG, its STAC item and its thumbnail together:

```
raster/{year}/collection.json
raster/{year}/zone={ZZ}/catalog.json
raster/{year}/zone={ZZ}/gzd={GZD}/catalog.json
raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif
raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.json
raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.thumb.png
```

So `01KFS_0_0` sits under `zone=01/gzd=01K/`. For bulk work, read a year's `items.parquet` mirror or the [index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet) rather than walking the tree.
