# UTM zone 42 — 2020

- 178 tiles in 6 grid zone designators: [`42Q`](./gzd=42Q/catalog.json), [`42R`](./gzd=42R/catalog.json), [`42S`](./gzd=42S/catalog.json), [`42T`](./gzd=42T/catalog.json), [`42U`](./gzd=42U/catalog.json), [`42V`](./gzd=42V/catalog.json)
- UTM zone 42, 2020. Parent collection: [FTW Global — Field & Boundary Probabilities 2020](../collection.json)

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Produced by Taylor Geospatial from the [Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Band semantics

Band 1 `field`, band 2 `boundary`; uint8 with scale 1/255, so probability = value × 1/255. 2.5 m, per-tile UTM CRS. The collection's [README](../../README.md) has the full band table.
