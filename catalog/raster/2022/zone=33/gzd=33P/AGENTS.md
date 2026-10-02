# AGENTS.md — Grid zone 33P — 2022

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 36 tiles, UTM zone 33, latitude band P, 2022.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `33PTK_0_0` to `33PZQ_0_0`.
- Parent: [UTM zone 33 — 2022](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2022/zone=33/gzd=33P/33PTK_0_0/33PTK_0_0.tif
```
