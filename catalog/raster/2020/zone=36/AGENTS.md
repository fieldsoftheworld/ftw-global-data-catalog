# AGENTS.md — UTM zone 36 — 2020

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 397 tiles in 12 grid zone designators: [`36J`](./gzd=36J/catalog.json), [`36K`](./gzd=36K/catalog.json), [`36L`](./gzd=36L/catalog.json), [`36M`](./gzd=36M/catalog.json), [`36N`](./gzd=36N/catalog.json), [`36P`](./gzd=36P/catalog.json), [`36Q`](./gzd=36Q/catalog.json), [`36R`](./gzd=36R/catalog.json), [`36S`](./gzd=36S/catalog.json), [`36T`](./gzd=36T/catalog.json), [`36U`](./gzd=36U/catalog.json), [`36V`](./gzd=36V/catalog.json)
- UTM zone 36, 2020. Parent collection: [FTW Global — Field & Boundary Probabilities 2020](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
