# AGENTS.md — Grid zone 20P — 2017

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 5 tiles, UTM zone 20, latitude band P, 2017.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `20PLQ_0_0` to `20PQB_0_0`.
- Parent: [UTM zone 20 — 2017](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2017/zone=20/gzd=20P/20PLQ_0_0/20PLQ_0_0.tif
```
