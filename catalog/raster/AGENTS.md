# AGENTS.md — FTW raster tree

Guidance for AI agents. Every claim here is measured from the index manifest or quoted from a verified `gdalinfo`.

- One collection per year, `2017/collection.json`, `2018/collection.json`, `2019/collection.json`, `2020/collection.json`, `2021/collection.json`, `2022/collection.json`, `2023/collection.json`, `2024/collection.json`, `2025/collection.json`.
- Enumerate tiles via the [index manifest](https://data.source.coop/ftw/global-data-beta/index/raster.parquet); read each year's AGENTS.md for band semantics.
- All years share the tile grid, so per-pixel year-over-year comparison works tile by tile.
