# UTM zone 53 — 2022

- 47 tiles in 6 grid zone designators: [`53H`](./gzd=53H/catalog.json), [`53K`](./gzd=53K/catalog.json), [`53L`](./gzd=53L/catalog.json), [`53S`](./gzd=53S/catalog.json), [`53T`](./gzd=53T/catalog.json), [`53U`](./gzd=53U/catalog.json)
- UTM zone 53, 2022. Parent collection: [FTW Global — Field & Boundary Probabilities 2022](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
