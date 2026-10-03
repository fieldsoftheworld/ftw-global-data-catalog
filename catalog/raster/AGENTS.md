# AGENTS.md — FTW raster tree

Guidance for AI agents. Every claim here is measured from the index manifest or quoted from a verified `gdalinfo`.

- One collection per year, `2017/collection.json`, `2018/collection.json`, `2019/collection.json`, `2020/collection.json`, `2021/collection.json`, `2022/collection.json`, `2023/collection.json`, `2024/collection.json`, `2025/collection.json`.
- Enumerate tiles via the [index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet); read each year's AGENTS.md for band semantics.
- `https://data.source.coop/ftw/global-data-2e/index/raster-lite.parquet` (110 kB) is the cheap tile finder: one row per (year, tile) with `year` int16, `tile_key`, `epsg` int32 and the WGS 84 bbox `xmin, ymin, xmax, ymax` as float32, rounded outward so a tile is never missed at its edge. It has no hrefs: a tile is at `raster/{year}/zone={tile_key[:2]}/gzd={tile_key[:3]}/{tile_key}/{tile_key}.tif`. Read `raster.parquet` for sizes and per-tile statistics.
- All years share the tile grid, so per-pixel year-over-year comparison works tile by tile.

<!-- known-limitation:begin -->
**Known limitation, under investigation: the 2017 and 2024 predictions are under-detected in some regions.**
<!-- known-limitation:end -->
