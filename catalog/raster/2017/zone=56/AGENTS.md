# AGENTS.md — UTM zone 56 — 2017

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 35 tiles in 3 grid zone designators: [`56H`](./gzd=56H/catalog.json), [`56J`](./gzd=56J/catalog.json), [`56K`](./gzd=56K/catalog.json)
- UTM zone 56, 2017. Parent collection: [FTW Global — Field & Boundary Probabilities 2017](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
