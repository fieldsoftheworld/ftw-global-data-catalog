# AGENTS.md — UTM zone 11 — 2017

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 91 tiles in 5 grid zone designators: [`11R`](./gzd=11R/catalog.json), [`11S`](./gzd=11S/catalog.json), [`11T`](./gzd=11T/catalog.json), [`11U`](./gzd=11U/catalog.json), [`11V`](./gzd=11V/catalog.json)
- UTM zone 11, 2017. Parent collection: [FTW Global — Field & Boundary Probabilities 2017](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
