# UTM zone 40 — 2023

- 107 tiles in 7 grid zone designators: [`40K`](./gzd=40K/catalog.json), [`40Q`](./gzd=40Q/catalog.json), [`40R`](./gzd=40R/catalog.json), [`40S`](./gzd=40S/catalog.json), [`40T`](./gzd=40T/catalog.json), [`40U`](./gzd=40U/catalog.json), [`40V`](./gzd=40V/catalog.json)
- UTM zone 40, 2023. Parent collection: [FTW Global — Field & Boundary Probabilities 2023](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
