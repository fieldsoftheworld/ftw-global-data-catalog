# AGENTS.md — Grid zone 46S — 2024

Guidance for AI agents. This is a grid-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 10 tiles, UTM zone 46, latitude band S, 2024.
- Each tile directory holds `{tile}.tif` (the COG), `{tile}.json` (its STAC item) and `{tile}.thumb.png`.
- Tile keys here run from `46SBC_0_0` to `46SGD_0_0`.
- Parent: [UTM zone 46 — 2024](../catalog.json).

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.

Read one tile:

```bash
gdalinfo /vsicurl/https://data.source.coop/ftw/global-data-beta/raster/2024/zone=46/gzd=46S/46SBC_0_0/46SBC_0_0.tif
```
