# UTM zone 12 — 2022

- 126 tiles in 5 grid zone designators: [`12J`](./gzd=12J/catalog.json), [`12R`](./gzd=12R/catalog.json), [`12S`](./gzd=12S/catalog.json), [`12T`](./gzd=12T/catalog.json), [`12U`](./gzd=12U/catalog.json)
- UTM zone 12, 2022. Parent collection: [FTW Global — Field & Boundary Probabilities 2022](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
