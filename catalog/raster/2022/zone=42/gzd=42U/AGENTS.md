# AGENTS.md — Grid zone 42U — 2022

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 34 tiles, UTM zone 42, latitude band U, 2022.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `42UUA_0_0` to `42UYV_0_0`.
- Parent: [UTM zone 42 — 2022](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2022/zone=42/gzd=42U/42UUA_0_0/42UUA_0_0.tif
```
