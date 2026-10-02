# AGENTS.md — Grid zone 18H — 2023

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 11 tiles, UTM zone 18, latitude band H, 2023.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `18HXB_0_0` to `18HYG_0_0`.
- Parent: [UTM zone 18 — 2023](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2023/zone=18/gzd=18H/18HXB_0_0/18HXB_0_0.tif
```
