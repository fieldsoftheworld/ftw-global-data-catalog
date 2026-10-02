# AGENTS.md — UTM zone 54 — 2024

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 91 tiles in 6 grid zone designators: [`54H`](./gzd=54H/catalog.json), [`54K`](./gzd=54K/catalog.json), [`54L`](./gzd=54L/catalog.json), [`54M`](./gzd=54M/catalog.json), [`54S`](./gzd=54S/catalog.json), [`54T`](./gzd=54T/catalog.json)
- UTM zone 54, 2024. Parent collection: [FTW Global — Field & Boundary Probabilities 2024](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
