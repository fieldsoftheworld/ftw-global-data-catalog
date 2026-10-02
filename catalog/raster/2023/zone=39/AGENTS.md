# AGENTS.md — UTM zone 39 — 2023

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 136 tiles in 9 grid zone designators: [`39K`](./gzd=39K/catalog.json), [`39L`](./gzd=39L/catalog.json), [`39P`](./gzd=39P/catalog.json), [`39Q`](./gzd=39Q/catalog.json), [`39R`](./gzd=39R/catalog.json), [`39S`](./gzd=39S/catalog.json), [`39T`](./gzd=39T/catalog.json), [`39U`](./gzd=39U/catalog.json), [`39V`](./gzd=39V/catalog.json)
- UTM zone 39, 2023. Parent collection: [FTW Global — Field & Boundary Probabilities 2023](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
