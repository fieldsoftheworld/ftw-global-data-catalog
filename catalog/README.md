# Fields of the World — Global Data (2nd Edition)

2nd Edition of the Fields of the World (FTW) global field-boundary
predictions on [Source Cooperative](https://source.coop/ftw/global-data-2e):
**1,139,401,371 predicted field polygons** over nine years as cloud-native
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
| [2017](./vector/2017/collection.json) | 113,556,951 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2018](./vector/2018/collection.json) | 120,266,836 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2019](./vector/2019/collection.json) | 122,156,582 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2020](./vector/2020/collection.json) | 129,366,600 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2021](./vector/2021/collection.json) | 136,149,304 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2022](./vector/2022/collection.json) | 130,296,544 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2023](./vector/2023/collection.json) | 133,227,819 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2024](./vector/2024/collection.json) | 120,295,636 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |
| [2025](./vector/2025/collection.json) | 134,085,099 | 54 UTM zones | GeoParquet (hive `zone=NN`, EPSG:4326), PMTiles, 4 styles |

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

One COG per Sentinel-2 MGRS-based tile at
`raster/{year}/zone={ZZ}/gzd={ZZL}/{tile_key}/{tile_key}.tif` (zone and grid zone
designator are the first two and three characters of the tile key; the tile's
STAC item and thumbnail sit beside it), each 40,032 × 40,032 pixels in its own
tile's UTM zone, with `field` (band 1) and `boundary` (band 2) probabilities as
uint8 scaled by 1/255, ZSTD, with overviews at 10–160 m. All years share one tile
grid, so year-over-year comparison works tile by tile. Enumerate tiles from the
[raster index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet)
or the 110 kB [lite index](#index-files); the
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
  2nd Edition, so a score of 80 is not an 80% chance that the parcel is real.
- **Weaker outside the training distribution.** FTW describes the confidence on
  its earlier global release as "conservative outside the FTW training
  distribution (e.g. smallholder systems): real fields there may receive low
  confidence" ([FTW](https://source.coop/ftw/global-data)). Expect the same
  shape of error here, and prefer a continuous `score` over a hard threshold in
  smallholder regions.
- **No land-cover masking.** Nothing upstream removed non-agricultural ground,
  so water, scrub and built-up land can appear as parcels. Parcels larger than
  5 km² were dropped in post-processing, and interior holes under 20 m²
  (pixel-scale polygonization artifacts) were filled.
- **Each year is an independent prediction.** Parcel `id` carries no meaning
  across years, so year-over-year comparison of the vectors needs a spatial
  join, not an id join. The rasters share one grid and compare per pixel.

<!-- known-limitation:begin -->
**Known limitation, under investigation: the 2017 and 2024 predictions are under-detected in some regions.**
<!-- known-limitation:end -->

## Coordinate systems

The vector GeoParquet is WGS 84 lon/lat (EPSG:4326) in **every** zone file —
the UTM zone is a partition key, not a CRS — so `ST_Area` on `geometry`
returns square degrees; read `metrics:area` (m²) instead. The COGs are each in
their own tile's UTM zone, so a mosaic across zones needs a warp. The PMTiles
archives are Web Mercator (EPSG:3857).

## Index files

Three manifests under `index/` list every data file, so nothing needs a bucket listing:

- [`index/vector.parquet`](https://data.source.coop/ftw/global-data-2e/index/vector.parquet) — one row
  per (year, UTM zone) file, 486 rows.
- [`index/raster.parquet`](https://data.source.coop/ftw/global-data-2e/index/raster.parquet) (1.9 MB) —
  one row per (year, tile), 67,197 rows.
- [`index/raster-lite.parquet`](https://data.source.coop/ftw/global-data-2e/index/raster-lite.parquet)
  (110 kB) — a slim copy of the raster index for viewers and quick "which tiles cover this box"
  lookups.

| `vector.parquet` column | Meaning |
|---|---|
| `year`, `zone` | mosaic year; UTM zone of the file (parcels whose centroid falls in it) |
| `href`, `s3_href`, `size_bytes` | HTTPS and S3 URLs of the file; its size |
| `n_parcels`, `area_km2` | parcels in the file; summed parcel area |
| `xmin, ymin, xmax, ymax`, `geometry` | WGS 84 bbox of the file's parcels, also as a polygon |

| `raster.parquet` column | Meaning |
|---|---|
| `year`, `tile_key`, `epsg` | mosaic year; Sentinel-2 MGRS tile id; UTM CRS of the COG |
| `href`, `s3_href`, `size_bytes` | HTTPS and S3 URLs of the COG; its size |
| `field_frac`, `boundary_frac` | share of pixels with p(field) > 0.5 and p(boundary) > 0.25, read from the coarsest overview |
| `cropland_frac` | IO land-cover cropland share of the tile (maximum over 2017, 2020, 2024) |
| `xmin, ymin, xmax, ymax`, `geometry` | WGS 84 footprint bbox of the tile, also as a polygon |

`raster-lite.parquet` has one row per (year, tile) and six columns: `year` (int16), `tile_key`,
`epsg` (int32) and the WGS 84 bbox `xmin, ymin, xmax, ymax` as float32, rounded outward so a tile
is never missed at its edge. It carries no hrefs: build them from the tile key (zone is its first
two characters, grid zone designator its first three):

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
lite = "https://data.source.coop/ftw/global-data-2e/index/raster-lite.parquet"
con.sql(f"""
    SELECT 'https://data.source.coop/ftw/global-data-2e/raster/' || year
           || '/zone=' || tile_key[1:2] || '/gzd=' || tile_key[1:3]
           || '/' || tile_key || '/' || tile_key || '.tif' AS href
    FROM read_parquet('{lite}')
    WHERE year = 2024 AND xmax >= -93.06 AND xmin <= -92.94
      AND ymax >= 41.90 AND ymin <= 42.00
""").show()
```

Use `raster.parquet` when you need sizes, the per-tile fractions or the footprint polygon.

## Reading an area across tiles

The COGs are one file per tile, so an area that crosses a tile edge needs a mosaic.
This reads the field and boundary probabilities for a lon/lat box at 20 m. `rasterio`
fetches the overview closest to `res`, not the 2.5 m data. Find the tiles in
[`index/raster.parquet`](https://data.source.coop/ftw/global-data-2e/index/raster.parquet):

```python
import duckdb, numpy as np, rasterio
from rasterio.merge import merge
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.enums import Resampling

def read_area(bbox, year, res, crs):
    """bbox = (lon_min, lat_min, lon_max, lat_max) -> (band, y, x) uint8 at `res` metres in `crs`."""
    x0, y0, x1, y1 = bbox
    hrefs = [h for (h,) in duckdb.sql(f"""
        select href from read_parquet('https://data.source.coop/ftw/global-data-2e/index/raster.parquet')
        where year={year} and xmax>={x0} and xmin<={x1} and ymax>={y0} and ymin<={y1}
        order by tile_key""").fetchall()]
    vrts = [WarpedVRT(rasterio.open(h), crs=crs, resampling=Resampling.nearest) for h in hrefs]
    l, b, r, t = transform_bounds("EPSG:4326", crs, *bbox)
    snap = lambda v, f: f(v / res) * res          # tile grids sit on whole multiples of res
    return merge(vrts, bounds=(snap(l, np.floor), snap(b, np.floor), snap(r, np.ceil), snap(t, np.ceil)),
                 res=res, method="first")

mosaic, transform = read_area((-93.06, 41.90, -92.94, 42.00), 2024, 20, "EPSG:32615")
# band 0 = field, band 1 = boundary; probability = value / 255
```

- Snap the bounds to the pixel size, as above. Otherwise the output grid sits a fraction of a
  pixel off the tile grid, and the values differ slightly from the COGs.
- Neighbouring tiles overlap by about 60 m, and each predicted that strip on its own.
  `method="first"` keeps the first tile's values there; use `"max"` or `"mean"` to combine them.
- `WarpedVRT` warps tiles from other UTM zones into `crs`. It leaves tiles already in `crs` as they are.

## Fixing this metadata

`catalog/` in
[fieldsoftheworld/ftw-global-data-catalog](https://github.com/fieldsoftheworld/ftw-global-data-catalog)
**is** this catalog: it syncs 1:1 to the bucket through `tools/publish.py`, so
a merged change lands here on the next publish. Publishing never deletes, and
no data bytes live in git — the repository carries only the metadata that
describes them. The vector tree is generated by `tools/build_vector_items.py`:
edit the generator and re-run it, never the generated output. The raster year
files are snapshots of the published objects, and the tile items and per-zone
catalogs under them live only in the bucket (see the repository's `CLAUDE.md`).
CI validates every change.

Report problems in the
[issue tracker](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).
