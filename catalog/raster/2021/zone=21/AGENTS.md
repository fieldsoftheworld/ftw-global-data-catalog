# AGENTS.md — UTM zone 21 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 217 tiles in 7 grid zone designators: [`21H`](./gzd=21H/catalog.json), [`21J`](./gzd=21J/catalog.json), [`21K`](./gzd=21K/catalog.json), [`21L`](./gzd=21L/catalog.json), [`21M`](./gzd=21M/catalog.json), [`21N`](./gzd=21N/catalog.json), [`21P`](./gzd=21P/catalog.json)
- UTM zone 21, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
