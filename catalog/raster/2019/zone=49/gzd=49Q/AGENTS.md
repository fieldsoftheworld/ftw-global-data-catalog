# AGENTS.md — Grid zone 49Q — 2019

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 32 tiles, UTM zone 49, latitude band Q, 2019.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `49QBA_0_0` to `49QHG_0_0`.
- Parent: [UTM zone 49 — 2019](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2019/zone=49/gzd=49Q/49QBA_0_0/49QBA_0_0.tif
```
