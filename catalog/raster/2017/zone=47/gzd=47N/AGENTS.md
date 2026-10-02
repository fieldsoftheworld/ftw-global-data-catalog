# AGENTS.md — Grid zone 47N — 2017

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 39 tiles, UTM zone 47, latitude band N, 2017.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `47NKD_0_0` to `47NRH_0_0`.
- Parent: [UTM zone 47 — 2017](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2017/zone=47/gzd=47N/47NKD_0_0/47NKD_0_0.tif
```
