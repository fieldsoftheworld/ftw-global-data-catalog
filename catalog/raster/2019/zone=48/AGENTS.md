# AGENTS.md — UTM zone 48 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 273 tiles in 8 grid zone designators: [`48M`](./gzd=48M/catalog.json), [`48N`](./gzd=48N/catalog.json), [`48P`](./gzd=48P/catalog.json), [`48Q`](./gzd=48Q/catalog.json), [`48R`](./gzd=48R/catalog.json), [`48S`](./gzd=48S/catalog.json), [`48T`](./gzd=48T/catalog.json), [`48U`](./gzd=48U/catalog.json)
- UTM zone 48, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
