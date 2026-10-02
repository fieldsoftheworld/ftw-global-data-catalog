# AGENTS.md — Grid zone 53L — 2020

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 1 tiles, UTM zone 53, latitude band L, 2020.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `53LKD_0_0` to `53LKD_0_0`.
- Parent: [UTM zone 53 — 2020](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2020/zone=53/gzd=53L/53LKD_0_0/53LKD_0_0.tif
```
