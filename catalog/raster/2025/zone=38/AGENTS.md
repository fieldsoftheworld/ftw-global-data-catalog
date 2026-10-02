# AGENTS.md — UTM zone 38 — 2025

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 260 tiles in 12 grid zone designators: [`38J`](./gzd=38J/catalog.json), [`38K`](./gzd=38K/catalog.json), [`38L`](./gzd=38L/catalog.json), [`38M`](./gzd=38M/catalog.json), [`38N`](./gzd=38N/catalog.json), [`38P`](./gzd=38P/catalog.json), [`38Q`](./gzd=38Q/catalog.json), [`38R`](./gzd=38R/catalog.json), [`38S`](./gzd=38S/catalog.json), [`38T`](./gzd=38T/catalog.json), [`38U`](./gzd=38U/catalog.json), [`38V`](./gzd=38V/catalog.json)
- UTM zone 38, 2025. Parent collection: [FTW Global — Field & Boundary Probabilities 2025](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
