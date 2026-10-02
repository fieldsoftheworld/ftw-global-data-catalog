# AGENTS.md — UTM zone 33 — 2020

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 231 tiles in 13 grid zone designators: [`33H`](./gzd=33H/catalog.json), [`33J`](./gzd=33J/catalog.json), [`33K`](./gzd=33K/catalog.json), [`33L`](./gzd=33L/catalog.json), [`33M`](./gzd=33M/catalog.json), [`33N`](./gzd=33N/catalog.json), [`33P`](./gzd=33P/catalog.json), [`33Q`](./gzd=33Q/catalog.json), [`33R`](./gzd=33R/catalog.json), [`33S`](./gzd=33S/catalog.json), [`33T`](./gzd=33T/catalog.json), [`33U`](./gzd=33U/catalog.json), [`33V`](./gzd=33V/catalog.json)
- UTM zone 33, 2020. Parent collection: [FTW Global — Field & Boundary Probabilities 2020](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
