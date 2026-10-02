# AGENTS.md — UTM zone 19 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 127 tiles in 10 grid zone designators: [`19G`](./gzd=19G/catalog.json), [`19H`](./gzd=19H/catalog.json), [`19J`](./gzd=19J/catalog.json), [`19K`](./gzd=19K/catalog.json), [`19L`](./gzd=19L/catalog.json), [`19N`](./gzd=19N/catalog.json), [`19P`](./gzd=19P/catalog.json), [`19Q`](./gzd=19Q/catalog.json), [`19T`](./gzd=19T/catalog.json), [`19U`](./gzd=19U/catalog.json)
- UTM zone 19, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
