# AGENTS.md — UTM zone 24 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 77 tiles in 3 grid zone designators: [`24K`](./gzd=24K/catalog.json), [`24L`](./gzd=24L/catalog.json), [`24M`](./gzd=24M/catalog.json)
- UTM zone 24, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
