# AGENTS.md — UTM zone 10 — 2024

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 59 tiles in 4 grid zone designators: [`10S`](./gzd=10S/catalog.json), [`10T`](./gzd=10T/catalog.json), [`10U`](./gzd=10U/catalog.json), [`10V`](./gzd=10V/catalog.json)
- UTM zone 10, 2024. Parent collection: [FTW Global — Field & Boundary Probabilities 2024](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
