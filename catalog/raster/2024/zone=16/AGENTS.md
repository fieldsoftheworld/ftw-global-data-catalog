# AGENTS.md — UTM zone 16 — 2024

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 139 tiles in 6 grid zone designators: [`16P`](./gzd=16P/catalog.json), [`16Q`](./gzd=16Q/catalog.json), [`16R`](./gzd=16R/catalog.json), [`16S`](./gzd=16S/catalog.json), [`16T`](./gzd=16T/catalog.json), [`16U`](./gzd=16U/catalog.json)
- UTM zone 16, 2024. Parent collection: [FTW Global — Field & Boundary Probabilities 2024](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
