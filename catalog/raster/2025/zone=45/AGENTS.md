# AGENTS.md — UTM zone 45 — 2025

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 118 tiles in 6 grid zone designators: [`45Q`](./gzd=45Q/catalog.json), [`45R`](./gzd=45R/catalog.json), [`45S`](./gzd=45S/catalog.json), [`45T`](./gzd=45T/catalog.json), [`45U`](./gzd=45U/catalog.json), [`45V`](./gzd=45V/catalog.json)
- UTM zone 45, 2025. Parent collection: [FTW Global — Field & Boundary Probabilities 2025](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
