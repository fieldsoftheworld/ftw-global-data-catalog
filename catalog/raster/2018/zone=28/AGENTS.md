# AGENTS.md — UTM zone 28 — 2018

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 46 tiles in 3 grid zone designators: [`28P`](./gzd=28P/catalog.json), [`28Q`](./gzd=28Q/catalog.json), [`28R`](./gzd=28R/catalog.json)
- UTM zone 28, 2018. Parent collection: [FTW Global — Field & Boundary Probabilities 2018](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
