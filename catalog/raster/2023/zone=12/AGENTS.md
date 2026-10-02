# AGENTS.md — UTM zone 12 — 2023

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 126 tiles in 5 grid zone designators: [`12J`](./gzd=12J/catalog.json), [`12R`](./gzd=12R/catalog.json), [`12S`](./gzd=12S/catalog.json), [`12T`](./gzd=12T/catalog.json), [`12U`](./gzd=12U/catalog.json)
- UTM zone 12, 2023. Parent collection: [FTW Global — Field & Boundary Probabilities 2023](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
