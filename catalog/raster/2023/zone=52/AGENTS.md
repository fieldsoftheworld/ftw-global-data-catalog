# AGENTS.md — UTM zone 52 — 2023

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 111 tiles in 9 grid zone designators: [`52K`](./gzd=52K/catalog.json), [`52L`](./gzd=52L/catalog.json), [`52M`](./gzd=52M/catalog.json), [`52N`](./gzd=52N/catalog.json), [`52R`](./gzd=52R/catalog.json), [`52S`](./gzd=52S/catalog.json), [`52T`](./gzd=52T/catalog.json), [`52U`](./gzd=52U/catalog.json), [`52V`](./gzd=52V/catalog.json)
- UTM zone 52, 2023. Parent collection: [FTW Global — Field & Boundary Probabilities 2023](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
