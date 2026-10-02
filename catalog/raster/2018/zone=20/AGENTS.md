# AGENTS.md — UTM zone 20 — 2018

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 209 tiles in 10 grid zone designators: [`20G`](./gzd=20G/catalog.json), [`20H`](./gzd=20H/catalog.json), [`20J`](./gzd=20J/catalog.json), [`20K`](./gzd=20K/catalog.json), [`20L`](./gzd=20L/catalog.json), [`20M`](./gzd=20M/catalog.json), [`20N`](./gzd=20N/catalog.json), [`20P`](./gzd=20P/catalog.json), [`20Q`](./gzd=20Q/catalog.json), [`20T`](./gzd=20T/catalog.json)
- UTM zone 20, 2018. Parent collection: [FTW Global — Field & Boundary Probabilities 2018](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
