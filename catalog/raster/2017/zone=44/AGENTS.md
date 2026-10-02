# AGENTS.md — UTM zone 44 — 2017

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 211 tiles in 8 grid zone designators: [`44N`](./gzd=44N/catalog.json), [`44P`](./gzd=44P/catalog.json), [`44Q`](./gzd=44Q/catalog.json), [`44R`](./gzd=44R/catalog.json), [`44S`](./gzd=44S/catalog.json), [`44T`](./gzd=44T/catalog.json), [`44U`](./gzd=44U/catalog.json), [`44V`](./gzd=44V/catalog.json)
- UTM zone 44, 2017. Parent collection: [FTW Global — Field & Boundary Probabilities 2017](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
