# AGENTS.md — Grid zone 20N — 2019

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 4 tiles, UTM zone 20, latitude band N, 2019.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `20NQH_0_0` to `20NRJ_0_0`.
- Parent: [UTM zone 20 — 2019](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2019/zone=20/gzd=20N/20NQH_0_0/20NQH_0_0.tif
```
