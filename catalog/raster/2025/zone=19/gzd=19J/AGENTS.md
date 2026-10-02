# AGENTS.md — Grid zone 19J — 2025

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 9 tiles, UTM zone 19, latitude band J, 2025.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `19JBF_0_0` to `19JGJ_0_0`.
- Parent: [UTM zone 19 — 2025](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2025/zone=19/gzd=19J/19JBF_0_0/19JBF_0_0.tif
```
