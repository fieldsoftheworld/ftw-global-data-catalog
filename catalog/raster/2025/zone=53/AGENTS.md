# AGENTS.md — UTM zone 53 — 2025

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 47 tiles in 6 grid zone designators: [`53H`](./gzd=53H/catalog.json), [`53K`](./gzd=53K/catalog.json), [`53L`](./gzd=53L/catalog.json), [`53S`](./gzd=53S/catalog.json), [`53T`](./gzd=53T/catalog.json), [`53U`](./gzd=53U/catalog.json)
- UTM zone 53, 2025. Parent collection: [FTW Global — Field & Boundary Probabilities 2025](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
