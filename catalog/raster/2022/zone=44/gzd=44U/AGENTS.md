# AGENTS.md — Grid zone 44U — 2022

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 34 tiles, UTM zone 44, latitude band U, 2022.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `44ULB_0_0` to `44UQV_0_0`.
- Parent: [UTM zone 44 — 2022](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2022/zone=44/gzd=44U/44ULB_0_0/44ULB_0_0.tif
```
