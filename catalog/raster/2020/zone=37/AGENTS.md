# AGENTS.md — UTM zone 37 — 2020

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 275 tiles in 11 grid zone designators: [`37K`](./gzd=37K/catalog.json), [`37L`](./gzd=37L/catalog.json), [`37M`](./gzd=37M/catalog.json), [`37N`](./gzd=37N/catalog.json), [`37P`](./gzd=37P/catalog.json), [`37Q`](./gzd=37Q/catalog.json), [`37R`](./gzd=37R/catalog.json), [`37S`](./gzd=37S/catalog.json), [`37T`](./gzd=37T/catalog.json), [`37U`](./gzd=37U/catalog.json), [`37V`](./gzd=37V/catalog.json)
- UTM zone 37, 2020. Parent collection: [FTW Global — Field & Boundary Probabilities 2020](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
