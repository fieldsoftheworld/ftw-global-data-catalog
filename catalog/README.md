# Fields of the World — Global Data (beta)

Beta release of the Fields of the World (FTW) global field-boundary
predictions on [Source Cooperative](https://source.coop/ftw/global-data-beta):
**383,570,287 predicted field polygons** over three years as cloud-native
GeoParquet, and **67,197 field/boundary-probability tiles** (26.0 TB) over nine
years as Cloud-Optimized GeoTIFFs. Both derive from the
[TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

Agents: [AGENTS.md](./AGENTS.md) beside this file is the agent guide. Read
[Limitations](#limitations) below before drawing conclusions from any of these
numbers.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

Two product families, one collection per year, so new years drop in
incrementally without reorganizing anything.

### [Vector](./vector/catalog.json) — field boundaries (GeoParquet)

| Collection | Parcels | Items | Formats |
|---|---|---|---|
| [2020](./vector/2020/collection.json) | 129,270,485 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2024](./vector/2024/collection.json) | 120,251,932 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2025](./vector/2025/collection.json) | 134,047,870 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |

One GeoParquet file per UTM zone at `vector/{year}/zone=NN/utm{NN}.parquet`,
plus a per-year PMTiles archive that hands over from A5 r7 cell aggregates to
the field polygons at z9. The nine-column schema follows
[fiboa](https://fiboa.org) and [vecorel](https://vecorel.org). Start at the
[vector README](./vector/README.md) for a runnable whole-collection query.

### [Raster](./raster/catalog.json) — field & boundary probability (COG)

| Collection | Tiles | Size | Formats |
|---|---|---|---|
| [2017](./raster/2017/collection.json) | 7,466 | 2.95 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2018](./raster/2018/collection.json) | 7,466 | 2.84 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2019](./raster/2019/collection.json) | 7,466 | 2.84 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2020](./raster/2020/collection.json) | 7,466 | 2.85 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2021](./raster/2021/collection.json) | 7,466 | 2.88 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2022](./raster/2022/collection.json) | 7,466 | 2.91 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2023](./raster/2023/collection.json) | 7,467 | 2.94 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2024](./raster/2024/collection.json) | 7,467 | 2.90 TB | two-band uint8 COG, 2.5 m, per-tile UTM |
| [2025](./raster/2025/collection.json) | 7,467 | 2.88 TB | two-band uint8 COG, 2.5 m, per-tile UTM |

One COG per Sentinel-2 MGRS-based tile at `raster/{year}/{tile_key}.tif`, each
40,032 × 40,032 pixels in its own tile's UTM zone, with `field` (band 1) and
`boundary` (band 2) probabilities as uint8 scaled by 1/255. All years share
one tile grid, so year-over-year comparison works tile by tile. Enumerate
tiles from the
[raster index manifest](https://data.source.coop/ftw/global-data-beta/index/raster.parquet),
which lists every tile with href, size, bbox and per-tile
field/boundary/cropland pixel fractions; the
[raster tree README](./raster/README.md) and each year's own README carry the
band semantics.

Both trees come from the same model on the same mosaics: the vectors are
instance polygons derived from 2.5 m field/boundary probabilities of the kind
the rasters publish. Treat them as two shapes of one prediction, not two
independent measurements.

## Limitations

These are **model predictions**, not a survey. In the FTW project's own words, a
field here is a *remote-sensing field unit* (a connected component of predicted
field-interior pixels), **not** a cadastral/legal parcel, and
[this is not a land-tenure product](https://source.coop/ftw/global-data); one
legal parcel may map to many polygons or to none. Parcel counts, areas and
perimeters are predicted quantities that carry the model's errors.

- **Model provenance.** The FTW `unet_balanced_fp32.onnx` model, run on the
  Sentinel-2 quarterly cloudless mosaics (B02/B03/B04/B08 × Q1–Q4, 10 m) to
  2.5 m field/boundary probabilities, then vectorized by BoundaryVote instance
  post-processing. Each collection's `description` records the exact chain; the
  checkpoint and its model card are released by the
  [FTW project](https://fieldsofthe.world) separately from this data.
- **`score` is a model probability, not a validated confidence.** The vector
  `score` column is the mean field probability the model assigned to the
  pixels inside the parcel, × 100 and rounded into a `uint8` (0–100). Use it to
  rank and filter; no calibration against ground truth is published for this
  beta, so a score of 80 is not an 80% chance that the parcel is real.
- **Weaker outside the training distribution.** FTW describes the confidence on
  its earlier global release as "conservative outside the FTW training
  distribution (e.g. smallholder systems): real fields there may receive low
  confidence" ([FTW](https://source.coop/ftw/global-data)). Expect the same
  shape of error here, and prefer a continuous `score` over a hard threshold in
  smallholder regions.
- **No land-cover masking.** Nothing upstream removed non-agricultural ground,
  so water, scrub and built-up land can appear as parcels. Parcels larger than
  5 km² were dropped in post-processing.
- **Each year is an independent prediction.** Parcel `id` carries no meaning
  across years, so year-over-year comparison of the vectors needs a spatial
  join, not an id join. The rasters share one grid and compare per pixel.

## Coordinate systems

The vector GeoParquet is WGS 84 lon/lat (EPSG:4326) in **every** zone file —
the UTM zone is a partition key, not a CRS — so `ST_Area` on `geometry`
returns square degrees; read `metrics:area` (m²) instead. The COGs are each in
their own tile's UTM zone, so a mosaic across zones needs a warp. The PMTiles
archives are Web Mercator (EPSG:3857).

## Fixing this metadata

`catalog/` in
[fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog)
**is** this catalog: it syncs 1:1 to the bucket through `tools/publish.py`, so
a merged change lands here on the next publish. Publishing never deletes, and
no data bytes live in git — the repository carries only the metadata that
describes them. The vector tree is generated by `tools/build_vector_items.py`
and the raster tree by `tools/build_raster_items.py`; edit the generator and
re-run it, never the generated output. CI validates every change.

Report problems in the
[issue tracker](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).
