# AGENTS.md — Grid zone 50H — 2019

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 20 tiles, UTM zone 50, latitude band H, 2019.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `50HLG_0_0` to `50HQK_0_0`.
- Parent: [UTM zone 50 — 2019](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2019/zone=50/gzd=50H/50HLG_0_0/50HLG_0_0.tif
```
