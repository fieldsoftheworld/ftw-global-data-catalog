# AGENTS.md — UTM zone 50 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 274 tiles in 12 grid zone designators: [`50H`](./gzd=50H/catalog.json), [`50J`](./gzd=50J/catalog.json), [`50K`](./gzd=50K/catalog.json), [`50L`](./gzd=50L/catalog.json), [`50M`](./gzd=50M/catalog.json), [`50N`](./gzd=50N/catalog.json), [`50P`](./gzd=50P/catalog.json), [`50Q`](./gzd=50Q/catalog.json), [`50R`](./gzd=50R/catalog.json), [`50S`](./gzd=50S/catalog.json), [`50T`](./gzd=50T/catalog.json), [`50U`](./gzd=50U/catalog.json)
- UTM zone 50, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
