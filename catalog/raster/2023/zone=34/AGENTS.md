# AGENTS.md — UTM zone 34 — 2023

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 185 tiles in 13 grid zone designators: [`34H`](./gzd=34H/catalog.json), [`34J`](./gzd=34J/catalog.json), [`34K`](./gzd=34K/catalog.json), [`34L`](./gzd=34L/catalog.json), [`34M`](./gzd=34M/catalog.json), [`34P`](./gzd=34P/catalog.json), [`34Q`](./gzd=34Q/catalog.json), [`34R`](./gzd=34R/catalog.json), [`34S`](./gzd=34S/catalog.json), [`34T`](./gzd=34T/catalog.json), [`34U`](./gzd=34U/catalog.json), [`34V`](./gzd=34V/catalog.json), [`34W`](./gzd=34W/catalog.json)
- UTM zone 34, 2023. Parent collection: [FTW Global — Field & Boundary Probabilities 2023](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
