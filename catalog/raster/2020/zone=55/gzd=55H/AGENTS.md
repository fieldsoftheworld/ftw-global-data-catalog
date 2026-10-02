# AGENTS.md — Grid zone 55H — 2020

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 42 tiles, UTM zone 55, latitude band H, 2020.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `55HBA_0_0` to `55HGV_0_0`.
- Parent: [UTM zone 55 — 2020](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2020/zone=55/gzd=55H/55HBA_0_0/55HBA_0_0.tif
```
