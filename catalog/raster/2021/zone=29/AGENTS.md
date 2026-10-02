# AGENTS.md — UTM zone 29 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 108 tiles in 6 grid zone designators: [`29N`](./gzd=29N/catalog.json), [`29P`](./gzd=29P/catalog.json), [`29R`](./gzd=29R/catalog.json), [`29S`](./gzd=29S/catalog.json), [`29T`](./gzd=29T/catalog.json), [`29U`](./gzd=29U/catalog.json)
- UTM zone 29, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
