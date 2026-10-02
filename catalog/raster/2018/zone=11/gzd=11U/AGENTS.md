# AGENTS.md — Grid zone 11U — 2018

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 27 tiles, UTM zone 11, latitude band U, 2018.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `11ULA_0_0` to `11UQV_0_0`.
- Parent: [UTM zone 11 — 2018](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2018/zone=11/gzd=11U/11ULA_0_0/11ULA_0_0.tif
```
