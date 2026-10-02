# AGENTS.md — UTM zone 42 — 2021

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 178 tiles in 6 grid zone designators: [`42Q`](./gzd=42Q/catalog.json), [`42R`](./gzd=42R/catalog.json), [`42S`](./gzd=42S/catalog.json), [`42T`](./gzd=42T/catalog.json), [`42U`](./gzd=42U/catalog.json), [`42V`](./gzd=42V/catalog.json)
- UTM zone 42, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
