# AGENTS.md — UTM zone 47 — 2024

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 217 tiles in 9 grid zone designators: [`47M`](./gzd=47M/catalog.json), [`47N`](./gzd=47N/catalog.json), [`47P`](./gzd=47P/catalog.json), [`47Q`](./gzd=47Q/catalog.json), [`47R`](./gzd=47R/catalog.json), [`47S`](./gzd=47S/catalog.json), [`47T`](./gzd=47T/catalog.json), [`47U`](./gzd=47U/catalog.json), [`47V`](./gzd=47V/catalog.json)
- UTM zone 47, 2024. Parent collection: [FTW Global — Field & Boundary Probabilities 2024](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
