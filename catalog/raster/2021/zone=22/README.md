# UTM zone 22 — 2021

- 199 tiles in 6 grid zone designators: [`22H`](./gzd=22H/catalog.json), [`22J`](./gzd=22J/catalog.json), [`22K`](./gzd=22K/catalog.json), [`22L`](./gzd=22L/catalog.json), [`22M`](./gzd=22M/catalog.json), [`22N`](./gzd=22N/catalog.json)
- UTM zone 22, 2021. Parent collection: [FTW Global — Field & Boundary Probabilities 2021](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
