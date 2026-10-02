# AGENTS.md — UTM zone 32 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 201 tiles in 11 grid zone designators: [`32L`](./gzd=32L/catalog.json), [`32M`](./gzd=32M/catalog.json), [`32N`](./gzd=32N/catalog.json), [`32P`](./gzd=32P/catalog.json), [`32Q`](./gzd=32Q/catalog.json), [`32R`](./gzd=32R/catalog.json), [`32S`](./gzd=32S/catalog.json), [`32T`](./gzd=32T/catalog.json), [`32U`](./gzd=32U/catalog.json), [`32V`](./gzd=32V/catalog.json), [`32W`](./gzd=32W/catalog.json)
- UTM zone 32, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
