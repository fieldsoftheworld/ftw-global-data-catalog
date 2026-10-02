# AGENTS.md — UTM zone 26 — 2025

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 4 tiles in 1 grid zone designators: [`26S`](./gzd=26S/catalog.json)
- UTM zone 26, 2025. Parent collection: [FTW Global — Field & Boundary Probabilities 2025](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
