# AGENTS.md — UTM zone 49 — 2025

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 217 tiles in 9 grid zone designators: [`49L`](./gzd=49L/catalog.json), [`49M`](./gzd=49M/catalog.json), [`49N`](./gzd=49N/catalog.json), [`49P`](./gzd=49P/catalog.json), [`49Q`](./gzd=49Q/catalog.json), [`49R`](./gzd=49R/catalog.json), [`49S`](./gzd=49S/catalog.json), [`49T`](./gzd=49T/catalog.json), [`49U`](./gzd=49U/catalog.json)
- UTM zone 49, 2025. Parent collection: [FTW Global — Field & Boundary Probabilities 2025](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
