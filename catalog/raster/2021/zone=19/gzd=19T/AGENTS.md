# AGENTS.md — Grid zone 19T — 2021

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 10 tiles, UTM zone 19, latitude band T, 2021.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `19TBG_0_0` to `19TFN_0_0`.
- Parent: [UTM zone 19 — 2021](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2021/zone=19/gzd=19T/19TBG_0_0/19TBG_0_0.tif
```
