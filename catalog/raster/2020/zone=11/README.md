# UTM zone 11 — 2020

- 91 tiles in 5 grid zone designators: [`11R`](./gzd=11R/catalog.json), [`11S`](./gzd=11S/catalog.json), [`11T`](./gzd=11T/catalog.json), [`11U`](./gzd=11U/catalog.json), [`11V`](./gzd=11V/catalog.json)
- UTM zone 11, 2020. Parent collection: [FTW Global — Field & Boundary Probabilities 2020](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
