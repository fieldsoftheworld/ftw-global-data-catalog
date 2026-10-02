# AGENTS.md — UTM zone 40 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 107 tiles in 7 grid zone designators: [`40K`](./gzd=40K/catalog.json), [`40Q`](./gzd=40Q/catalog.json), [`40R`](./gzd=40R/catalog.json), [`40S`](./gzd=40S/catalog.json), [`40T`](./gzd=40T/catalog.json), [`40U`](./gzd=40U/catalog.json), [`40V`](./gzd=40V/catalog.json)
- UTM zone 40, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
