# AGENTS.md — UTM zone 22 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 199 tiles in 6 grid zone designators: [`22H`](./gzd=22H/catalog.json), [`22J`](./gzd=22J/catalog.json), [`22K`](./gzd=22K/catalog.json), [`22L`](./gzd=22L/catalog.json), [`22M`](./gzd=22M/catalog.json), [`22N`](./gzd=22N/catalog.json)
- UTM zone 22, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
