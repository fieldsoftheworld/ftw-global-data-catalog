# AGENTS.md — Grid zone 35M — 2017

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 17 tiles, UTM zone 35, latitude band M, 2017.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `35MKM_0_0` to `35MRV_0_0`.
- Parent: [UTM zone 35 — 2017](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2017/zone=35/gzd=35M/35MKM_0_0/35MKM_0_0.tif
```
