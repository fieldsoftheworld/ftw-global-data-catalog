# AGENTS.md — Grid zone 15S — 2023

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 50 tiles, UTM zone 15, latitude band S, 2023.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `15STA_0_0` to `15SYV_0_0`.
- Parent: [UTM zone 15 — 2023](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2023/zone=15/gzd=15S/15STA_0_0/15STA_0_0.tif
```
