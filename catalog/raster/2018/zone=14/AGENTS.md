# AGENTS.md — UTM zone 14 — 2018

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 212 tiles in 6 grid zone designators: [`14P`](./gzd=14P/catalog.json), [`14Q`](./gzd=14Q/catalog.json), [`14R`](./gzd=14R/catalog.json), [`14S`](./gzd=14S/catalog.json), [`14T`](./gzd=14T/catalog.json), [`14U`](./gzd=14U/catalog.json)
- UTM zone 14, 2018. Parent collection: [FTW Global — Field & Boundary Probabilities 2018](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
