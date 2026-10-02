# AGENTS.md — UTM zone 51 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 199 tiles in 10 grid zone designators: [`51H`](./gzd=51H/catalog.json), [`51L`](./gzd=51L/catalog.json), [`51M`](./gzd=51M/catalog.json), [`51N`](./gzd=51N/catalog.json), [`51P`](./gzd=51P/catalog.json), [`51Q`](./gzd=51Q/catalog.json), [`51R`](./gzd=51R/catalog.json), [`51S`](./gzd=51S/catalog.json), [`51T`](./gzd=51T/catalog.json), [`51U`](./gzd=51U/catalog.json)
- UTM zone 51, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
