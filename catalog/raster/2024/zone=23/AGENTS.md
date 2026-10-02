# AGENTS.md — UTM zone 23 — 2024

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 136 tiles in 4 grid zone designators: [`23J`](./gzd=23J/catalog.json), [`23K`](./gzd=23K/catalog.json), [`23L`](./gzd=23L/catalog.json), [`23M`](./gzd=23M/catalog.json)
- UTM zone 23, 2024. Parent collection: [FTW Global — Field & Boundary Probabilities 2024](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
