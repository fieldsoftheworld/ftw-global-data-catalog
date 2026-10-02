# AGENTS.md — Grid zone 23L — 2020

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 49 tiles, UTM zone 23, latitude band L, 2020.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `23LKC_0_0` to `23LRK_0_0`.
- Parent: [UTM zone 23 — 2020](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2020/zone=23/gzd=23L/23LKC_0_0/23LKC_0_0.tif
```
