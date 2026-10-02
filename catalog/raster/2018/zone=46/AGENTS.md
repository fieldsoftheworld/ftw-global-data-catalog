# AGENTS.md — UTM zone 46 — 2018

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 111 tiles in 8 grid zone designators: [`46N`](./gzd=46N/catalog.json), [`46P`](./gzd=46P/catalog.json), [`46Q`](./gzd=46Q/catalog.json), [`46R`](./gzd=46R/catalog.json), [`46S`](./gzd=46S/catalog.json), [`46T`](./gzd=46T/catalog.json), [`46U`](./gzd=46U/catalog.json), [`46V`](./gzd=46V/catalog.json)
- UTM zone 46, 2018. Parent collection: [FTW Global — Field & Boundary Probabilities 2018](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
