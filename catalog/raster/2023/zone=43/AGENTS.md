# AGENTS.md — UTM zone 43 — 2023

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 231 tiles in 7 grid zone designators: [`43P`](./gzd=43P/catalog.json), [`43Q`](./gzd=43Q/catalog.json), [`43R`](./gzd=43R/catalog.json), [`43S`](./gzd=43S/catalog.json), [`43T`](./gzd=43T/catalog.json), [`43U`](./gzd=43U/catalog.json), [`43V`](./gzd=43V/catalog.json)
- UTM zone 43, 2023. Parent collection: [FTW Global — Field & Boundary Probabilities 2023](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
