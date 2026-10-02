# UTM zone 28 — 2022

- 46 tiles in 3 grid zone designators: [`28P`](./gzd=28P/catalog.json), [`28Q`](./gzd=28Q/catalog.json), [`28R`](./gzd=28R/catalog.json)
- UTM zone 28, 2022. Parent collection: [FTW Global — Field & Boundary Probabilities 2022](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
