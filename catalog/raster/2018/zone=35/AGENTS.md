# AGENTS.md — UTM zone 35 — 2018

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 320 tiles in 14 grid zone designators: [`35H`](./gzd=35H/catalog.json), [`35J`](./gzd=35J/catalog.json), [`35K`](./gzd=35K/catalog.json), [`35L`](./gzd=35L/catalog.json), [`35M`](./gzd=35M/catalog.json), [`35N`](./gzd=35N/catalog.json), [`35P`](./gzd=35P/catalog.json), [`35Q`](./gzd=35Q/catalog.json), [`35R`](./gzd=35R/catalog.json), [`35S`](./gzd=35S/catalog.json), [`35T`](./gzd=35T/catalog.json), [`35U`](./gzd=35U/catalog.json), [`35V`](./gzd=35V/catalog.json), [`35W`](./gzd=35W/catalog.json)
- UTM zone 35, 2018. Parent collection: [FTW Global — Field & Boundary Probabilities 2018](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
