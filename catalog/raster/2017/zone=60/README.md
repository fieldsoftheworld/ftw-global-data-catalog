# UTM zone 60 — 2017

- 22 tiles in 3 grid zone designators: [`60G`](./gzd=60G/catalog.json), [`60H`](./gzd=60H/catalog.json), [`60K`](./gzd=60K/catalog.json)
- UTM zone 60, 2017. Parent collection: [FTW Global — Field & Boundary Probabilities 2017](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
