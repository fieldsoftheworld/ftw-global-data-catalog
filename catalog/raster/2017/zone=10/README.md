# UTM zone 10 — 2017

- 59 tiles in 4 grid zone designators: [`10S`](./gzd=10S/catalog.json), [`10T`](./gzd=10T/catalog.json), [`10U`](./gzd=10U/catalog.json), [`10V`](./gzd=10V/catalog.json)
- UTM zone 10, 2017. Parent collection: [FTW Global — Field & Boundary Probabilities 2017](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
