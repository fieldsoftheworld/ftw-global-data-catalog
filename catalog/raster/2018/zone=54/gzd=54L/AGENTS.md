# AGENTS.md — Grid zone 54L — 2018

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 1 tiles, UTM zone 54, latitude band L, 2018.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `54LVR_0_0` to `54LVR_0_0`.
- Parent: [UTM zone 54 — 2018](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2018/zone=54/gzd=54L/54LVR_0_0/54LVR_0_0.tif
```
