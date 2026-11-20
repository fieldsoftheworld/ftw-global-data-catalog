# AGENTS.md — FTW raster tree

The 2018, 2020, 2021, 2022, 2023, 2024 and 2025 collections use the updated processing documented in their year collections. Other year links retain historical published collections; they do not imply completion of the updated recipe for those years. Consult each collection for its provenance, footprint and asset inventory.

Guidance for AI agents. Every claim here is measured from the index manifest or quoted from a verified COG header.

Related guides: the [catalog root](../AGENTS.md), the sibling [vector tree](../vector/AGENTS.md), and each year's own AGENTS.md ([2017](./2017/AGENTS.md), [2018](./2018/AGENTS.md), [2019](./2019/AGENTS.md), [2020](./2020/AGENTS.md), [2021](./2021/AGENTS.md), [2022](./2022/AGENTS.md), [2023](./2023/AGENTS.md), [2024](./2024/AGENTS.md), [2025](./2025/AGENTS.md)).

- One collection per year, `2017/collection.json`, `2018/collection.json`, `2019/collection.json`, `2020/collection.json`, `2021/collection.json`, `2022/collection.json`, `2023/collection.json`, `2024/collection.json`, `2025/collection.json`.
- Layout: `raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/` holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item); `raster/{year}/` holds `collection.json`, `items.parquet`, and zone catalogs. The updated 2018, 2020, 2021, 2022, 2023, 2024 and 2025 collections declare a global overview GeoTIFF and WebP thumbnail. For other years, follow the assets declared by their collections.
- Enumerate tiles via the [index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet) or a year's `items.parquet`, never by listing the bucket and never by walking the browse tree (590 catalogs for 2018; 596 catalogs for 2022; 597 catalogs for 2021; 596 catalogs for 2020; 601 catalogs for 2023; 600 catalogs for each updated 2024/2025 year); read each year's AGENTS.md for band semantics.
- Band 1 `field`, band 2 `boundary`; uint8, probability = value / 255. No nodata is declared.
- All years share the tile grid, so per-pixel year-over-year comparison works tile by tile.

## Changing this tree

Preserve source identities and completed artifacts. Update the relevant versioned metadata adapter and validate its output against the current year collection before publication. Do not regenerate raster predictions to edit metadata.
