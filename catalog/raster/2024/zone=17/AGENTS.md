# AGENTS.md — UTM zone 17 — 2024

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 162 tiles in 9 grid zone designators: [`17L`](./gzd=17L/catalog.json), [`17M`](./gzd=17M/catalog.json), [`17N`](./gzd=17N/catalog.json), [`17P`](./gzd=17P/catalog.json), [`17Q`](./gzd=17Q/catalog.json), [`17R`](./gzd=17R/catalog.json), [`17S`](./gzd=17S/catalog.json), [`17T`](./gzd=17T/catalog.json), [`17U`](./gzd=17U/catalog.json)
- UTM zone 17, 2024. Parent collection: [FTW Global — Field & Boundary Probabilities 2024](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
