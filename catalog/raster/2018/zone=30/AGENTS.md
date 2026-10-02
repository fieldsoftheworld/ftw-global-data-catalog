# AGENTS.md — UTM zone 30 — 2018

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 169 tiles in 8 grid zone designators: [`30N`](./gzd=30N/catalog.json), [`30P`](./gzd=30P/catalog.json), [`30Q`](./gzd=30Q/catalog.json), [`30R`](./gzd=30R/catalog.json), [`30S`](./gzd=30S/catalog.json), [`30T`](./gzd=30T/catalog.json), [`30U`](./gzd=30U/catalog.json), [`30V`](./gzd=30V/catalog.json)
- UTM zone 30, 2018. Parent collection: [FTW Global — Field & Boundary Probabilities 2018](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
