# AGENTS.md — Grid zone 51R — 2020

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 13 tiles, UTM zone 51, latitude band R, 2020.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `51RTH_0_0` to `51RYH_0_0`.
- Parent: [UTM zone 51 — 2020](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2020/zone=51/gzd=51R/51RTH_0_0/51RTH_0_0.tif
```
