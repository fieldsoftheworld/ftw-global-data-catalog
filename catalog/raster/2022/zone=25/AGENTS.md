# AGENTS.md — UTM zone 25 — 2022

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 6 tiles in 2 grid zone designators: [`25L`](./gzd=25L/catalog.json), [`25M`](./gzd=25M/catalog.json)
- UTM zone 25, 2022. Parent collection: [FTW Global — Field & Boundary Probabilities 2022](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
