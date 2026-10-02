# AGENTS.md — UTM zone 18 — 2019

Guidance for AI agents. This is a UTM-zone browse catalog: it exists so the items are reachable by `child`/`item` links. Every number here is measured from `index/raster.parquet`.

- 173 tiles in 11 grid zone designators: [`18G`](./gzd=18G/catalog.json), [`18H`](./gzd=18H/catalog.json), [`18K`](./gzd=18K/catalog.json), [`18L`](./gzd=18L/catalog.json), [`18M`](./gzd=18M/catalog.json), [`18N`](./gzd=18N/catalog.json), [`18P`](./gzd=18P/catalog.json), [`18Q`](./gzd=18Q/catalog.json), [`18S`](./gzd=18S/catalog.json), [`18T`](./gzd=18T/catalog.json), [`18U`](./gzd=18U/catalog.json)
- UTM zone 18, 2019. Parent collection: [FTW Global — Field & Boundary Probabilities 2019](../collection.json)

- Probability = pixel value × 1/255; band 1 `field`, band 2 `boundary`; no nodata is declared.
- To enumerate tiles in bulk, query the collection's `items.parquet` mirror or the index manifest rather than walking these catalogs.
