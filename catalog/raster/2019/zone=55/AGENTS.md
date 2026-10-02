# AGENTS.md — UTM zone 55 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 109 tiles in 6 grid zone designators: [`55G`](./gzd=55G/catalog.json), [`55H`](./gzd=55H/catalog.json), [`55J`](./gzd=55J/catalog.json), [`55K`](./gzd=55K/catalog.json), [`55M`](./gzd=55M/catalog.json), [`55T`](./gzd=55T/catalog.json)
- UTM zone 55, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
