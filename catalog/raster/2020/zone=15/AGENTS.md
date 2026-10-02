# AGENTS.md — UTM zone 15 — 2020

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 147 tiles in 6 grid zone designators: [`15P`](./gzd=15P/catalog.json), [`15Q`](./gzd=15Q/catalog.json), [`15R`](./gzd=15R/catalog.json), [`15S`](./gzd=15S/catalog.json), [`15T`](./gzd=15T/catalog.json), [`15U`](./gzd=15U/catalog.json)
- UTM zone 15, 2020. Parent collection: [FTW Global — Field & Boundary Probabilities 2020](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
