# AGENTS.md — UTM zone 31 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 170 tiles in 7 grid zone designators: [`31N`](./gzd=31N/catalog.json), [`31P`](./gzd=31P/catalog.json), [`31Q`](./gzd=31Q/catalog.json), [`31R`](./gzd=31R/catalog.json), [`31S`](./gzd=31S/catalog.json), [`31T`](./gzd=31T/catalog.json), [`31U`](./gzd=31U/catalog.json)
- UTM zone 31, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
