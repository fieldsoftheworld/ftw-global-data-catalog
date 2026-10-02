# AGENTS.md — UTM zone 41 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 110 tiles in 5 grid zone designators: [`41R`](./gzd=41R/catalog.json), [`41S`](./gzd=41S/catalog.json), [`41T`](./gzd=41T/catalog.json), [`41U`](./gzd=41U/catalog.json), [`41V`](./gzd=41V/catalog.json)
- UTM zone 41, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
