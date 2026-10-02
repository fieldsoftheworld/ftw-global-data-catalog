# AGENTS.md — Grid zone 36U — 2021

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 43 tiles, UTM zone 36, latitude band U, 2021.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `36UUA_0_0` to `36UYV_0_0`.
- Parent: [UTM zone 36 — 2021](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2021/zone=36/gzd=36U/36UUA_0_0/36UUA_0_0.tif
```
