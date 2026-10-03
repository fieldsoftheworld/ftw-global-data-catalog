# Fields of the World — Global Data (2nd Edition)

2nd Edition of the Fields of the World (FTW) global field-boundary
predictions: **1,139,401,371 predicted field polygons** over nine years as
cloud-native GeoParquet, and **67,197 field/boundary-probability tiles**
(26.0 TB) over the same nine years as Cloud-Optimized GeoTIFFs. Both derive
from the
[TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/),
and both are part of [Fields of the World](https://fieldsofthe.world).

## Three ways in

**[Open the interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/)**
to see the fields themselves. It streams the PMTiles archives and the
Sentinel-2 quarterly mosaics straight from Source Cooperative, so you can pan
to a region, switch between years, and compare predictions against the imagery
they came from without downloading anything.

**[Open the catalog in the Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/catalog.json)**
to walk the STAC tree. The browser renders each collection with its own
published style, previews the GeoParquet schema, and gives the download link
for every asset. It is the fastest way to find out what a given year actually
contains.

**[Browse the files on Source Cooperative](https://source.coop/ftw/global-data-2e)**
to download them directly, or read them in place over HTTP range requests from
`https://data.source.coop/ftw/global-data-2e/`.

Agents: [AGENTS.md](https://data.source.coop/ftw/global-data-2e/AGENTS.md) beside this file is the agent guide. Read
[Limitations](#limitations) before drawing conclusions from any of these
numbers.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

Two product families, one collection per year, so new years drop in
incrementally without reorganizing anything. Every year has both products on
the same footprint.

| Year | Parcels | Vector (GeoParquet) | Tiles | Raster (COG) | Map |
|---|---|---|---|---|---|
| 2017 | 113,556,951 | [files](https://source.coop/ftw/global-data-2e/vector/2017) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2017/collection.json) | 7,466 (2.95 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2017) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2017/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2017) |
| 2018 | 120,266,836 | [files](https://source.coop/ftw/global-data-2e/vector/2018) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2018/collection.json) | 7,466 (2.84 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2018) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2018/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2018) |
| 2019 | 122,156,582 | [files](https://source.coop/ftw/global-data-2e/vector/2019) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2019/collection.json) | 7,466 (2.84 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2019) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2019/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2019) |
| 2020 | 129,366,600 | [files](https://source.coop/ftw/global-data-2e/vector/2020) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2020/collection.json) | 7,466 (2.85 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2020) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2020/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2020) |
| 2021 | 136,149,304 | [files](https://source.coop/ftw/global-data-2e/vector/2021) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2021/collection.json) | 7,466 (2.88 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2021) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2021/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2021) |
| 2022 | 130,296,544 | [files](https://source.coop/ftw/global-data-2e/vector/2022) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2022/collection.json) | 7,466 (2.91 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2022) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2022/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2022) |
| 2023 | 133,227,819 | [files](https://source.coop/ftw/global-data-2e/vector/2023) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2023/collection.json) | 7,467 (2.94 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2023) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2023/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2023) |
| 2024 | 120,295,636 | [files](https://source.coop/ftw/global-data-2e/vector/2024) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2024/collection.json) | 7,467 (2.90 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2024) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2024/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2024) |
| 2025 | 134,085,099 | [files](https://source.coop/ftw/global-data-2e/vector/2025) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2025/collection.json) | 7,467 (2.88 TB) | [files](https://source.coop/ftw/global-data-2e/raster/2025) · [browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/raster/2025/collection.json) | [open](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2025) |

The **vector** product is one GeoParquet file per UTM zone at
`vector/{year}/zone=NN/utm{NN}.parquet`, 54 zones per year, hive-partitioned
so a whole year reads as one dataset. Its nine-column schema follows
[fiboa](https://fiboa.org) and [vecorel](https://vecorel.org). A per-year
PMTiles archive hands over from A5 r7 cell aggregates to the field polygons at
z9, and four MapLibre styles ship beside it. The
[vector README](https://source.coop/ftw/global-data-2e/vector) has a runnable whole-collection query.

The **raster** product is one COG per Sentinel-2 MGRS-based tile at
`raster/{year}/zone={ZZ}/gzd={ZZL}/{tile_key}/{tile_key}.tif` (zone and grid zone
designator are the first two and three characters of the tile key; the tile's
STAC item and thumbnail sit beside it), each 40,032 × 40,032 pixels at 2.5 m in
its own tile's UTM zone, with `field` (band 1) and `boundary` (band 2)
probabilities as uint8 scaled by 1/255, ZSTD, with overviews at 10–160 m. All
years share one tile grid, so year-over-year comparison works tile by tile.
Enumerate tiles from the
[raster index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet),
which lists every tile with href, size, bbox and per-tile
field/boundary/cropland pixel fractions, or from the 110 kB
[lite index](#index-files). The
[raster README](https://source.coop/ftw/global-data-2e/raster) and each year's own README carry the band
semantics.

Both trees come from the same model on the same mosaics. The vectors are
instance polygons derived from 2.5 m field/boundary probabilities of the kind
the rasters publish, so treat them as two shapes of one prediction rather than
two independent measurements.

## How this was made

The FTW `unet_balanced_fp32.onnx` model runs on Sentinel-2 quarterly cloudless
mosaics, taking 16 input bands per tile from B04, B03, B02 and B08 across
quarters Q1 to Q4 at 10 m. It emits field and boundary probabilities at 2.5 m,
which are published directly as the raster product. BoundaryVote instance
post-processing then turns those probabilities into polygons, which are
simplified at 5 m across the whole coverage in one pass, filtered to parcels
between 900 m² and 5 km², joined across tile seams, and written as the vector
product.

The whole chain lives in this catalog's own repository, and
[pipeline/README.md](https://github.com/fieldsoftheworld/ftw-global-data-catalog/blob/main/pipeline/README.md)
documents every stage, from the mosaic download through inference and
post-processing to the map tiles and this metadata.

## Limitations

These are **model predictions**, not a survey. A field here is a
*remote-sensing field unit*, a connected component of predicted field-interior
pixels, and **not** a cadastral or legal parcel. This is not a land-tenure
product, and one legal parcel may map to many polygons or to none. Parcel
counts, areas and perimeters are predicted quantities that carry the model's
errors. See [Fields of the World](https://fieldsofthe.world) for the project
and its definitions.

- The `score` column is a model probability, not a validated confidence. It is
  the mean field probability the model assigned to the pixels inside the
  parcel, multiplied by 100 and rounded into a `uint8` from 0 to 100. Use it to
  rank and filter, but no calibration against ground truth is published for
  this 2nd Edition, so a score of 80 is not an 80% chance that the parcel is
  real.
- Predictions are weaker outside the training distribution. FTW describes the
  confidence on its earlier global release as "conservative outside the FTW
  training distribution (e.g. smallholder systems): real fields there may
  receive low confidence". Expect the same shape of error here, and prefer a
  continuous `score` over a hard threshold in smallholder regions.
- Coverage is cropland-gated, so it is not global. Only MGRS tiles with at
  least 1% cropland were processed: every year's minimum `cropland_frac` in
  the raster index is 0.010006, with no tile below it. Open desert, ice, dense
  forest and purely urban tiles are therefore absent by construction, and an
  empty region means the pipeline never ran there rather than that it found no
  fields.
- Inside a processed tile, nothing is filtered out by land cover. Water, scrub
  and built-up ground can carry predicted parcels, so mask them downstream if
  your analysis needs them gone. A parcel is removed only for being smaller
  than 900 m², larger than 5 km², or owned by a neighboring tile; interior holes
  under 20 m² (pixel-scale polygonization artifacts) were filled. Land cover
  and terrain are read per parcel, but only into quality attributes that stay
  in the intermediate files and are not published.
- Each year is an independent prediction. A parcel `id` carries no meaning
  across years, so comparing years in the vectors needs a spatial join rather
  than an id join. The rasters share one grid and compare per pixel.

<!-- known-limitation:begin -->
**Known limitation, under investigation: the 2017 and 2024 predictions are under-detected in some regions.**
<!-- known-limitation:end -->

## Coordinate systems

The vector GeoParquet is WGS 84 lon/lat (EPSG:4326) in **every** zone file,
because the UTM zone is a partition key and not a CRS. `ST_Area` on `geometry`
therefore returns square degrees, so read `metrics:area` (m²) instead. The COGs
are each in their own tile's UTM zone, so a mosaic across zones needs a warp.
The PMTiles archives are Web Mercator (EPSG:3857).

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

The COGs are one file per tile, so an area that crosses a tile edge needs a
mosaic. This reads the field and boundary probabilities for a lon/lat box at
20 m. `rasterio` fetches the overview closest to `res`, not the 2.5 m data.
Find the tiles in
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
**is** this catalog. It syncs 1:1 to the bucket through `tools/publish.py`, so
a merged change lands here on the next publish. That repository also holds the
processing pipeline that produced the data. Publishing never deletes, and no
data bytes live in git, so the repository carries only the metadata that
describes them. The vector tree is generated by `tools/build_vector_items.py`:
edit the generator and re-run it, never the generated output. The raster year
files are snapshots of the published objects, and the tile items and per-zone
catalogs under them live only in the bucket (see the repository's `CLAUDE.md`).
CI validates every change.

Report problems in the
[issue tracker](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).
