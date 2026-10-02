# AGENTS.md — Grid zone 45Q — 2024

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 32 tiles, UTM zone 45, latitude band Q, 2024.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `45QTA_0_0` to `45QZG_0_0`.
- Parent: [UTM zone 45 — 2024](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2024/zone=45/gzd=45Q/45QTA_0_0/45QTA_0_0.tif
```
