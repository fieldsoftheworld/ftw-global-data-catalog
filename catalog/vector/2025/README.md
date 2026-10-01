# FTW Global — Field Boundaries 2025 (GeoParquet)

Predicted agricultural field boundaries for 2025: **134,047,870 parcels** in 54 per-UTM-zone GeoParquet files (112.0 GiB). Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Browse it in the [data browser](https://source.coop/ftw/global-data-beta); read [AGENTS.md](./AGENTS.md) beside this file if you are an agent, and [Limitations](#limitations) below before you draw conclusions from the numbers.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Query it

One zone, straight over https — no download, no credentials:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-beta/vector/2025/zone=31/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg(score), 1) AS avg_score
    FROM read_parquet('{url}')
""").show()
```

## Whole year, every zone

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute("""
    CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2',
                   URL_STYLE 'path')
""")
glob = "s3://us-west-2.opendata.source.coop/ftw/global-data-beta/vector/2025/zone=*/utm*.parquet"
con.sql(f"""
    SELECT zone, count(*) AS parcels
    FROM read_parquet('{glob}', hive_partitioning=1)
    GROUP BY zone ORDER BY parcels DESC LIMIT 5
""").show()
```

The glob needs the `s3://` endpoint — DuckDB cannot expand a wildcard in an http URL — and Source Cooperative needs `URL_STYLE 'path'`. The bucket is anonymous-read, so the secret carries no credentials. `hive_partitioning=1` is what turns the `zone=NN` directory into a `zone` column; it arrives as a string, zero-padded, so compare it as `zone = '31'`. Swap the year in the glob to read a different collection.

## Columns

The schema follows [fiboa 0.3.0](https://fiboa.org/specification/v0.3.0/schema.yaml) and [vecorel 0.1.0](https://vecorel.org/specification/v0.1.0/schema.yaml) (also machine-readable in each item's `table:columns`):

| Column | Description |
|---|---|
| `id` | Parcel identifier, unique per collection ([fiboa 0.3.0](https://fiboa.org/specification/v0.3.0/schema.yaml)). |
| `collection` | The vecorel collection the parcel belongs to (constant per year, e.g. `ftw-s2-2025`). |
| `geometry` | Parcel footprint (WKB Polygon/MultiPolygon, WGS 84). |
| `bbox` | Bounding box of the parcel (xmin, ymin, xmax, ymax). |
| `metrics:area` | Area of the parcel in square meters ([vecorel 0.1.0](https://vecorel.org/specification/v0.1.0/schema.yaml) geometry-metrics). |
| `metrics:perimeter` | Perimeter of the parcel in meters ([vecorel 0.1.0](https://vecorel.org/specification/v0.1.0/schema.yaml) geometry-metrics). |
| `score` | Mean model field probability inside the parcel, × 100 rounded (0–100). |
| `determination:datetime` | The prediction year's UTC start marker, constant per year ([fiboa 0.3.0](https://fiboa.org/specification/v0.3.0/schema.yaml)). |
| `determination:method` | Constant `auto-imagery` ([fiboa 0.3.0](https://fiboa.org/specification/v0.3.0/schema.yaml)). |

## Coordinate system

Every zone file stores `geometry` as WGS 84 lon/lat (**EPSG:4326**), not in its UTM zone — the zone is a partition key, so a cross-zone or whole-year read needs no reprojection and the `bbox` struct can be compared across zones directly. Because the coordinates are degrees, `ST_Area` and `ST_Length` on `geometry` return degree-based numbers that mean nothing on the ground: read `metrics:area` (m²) and `metrics:perimeter` (m) instead, or reproject to an equal-area CRS first.

The PMTiles archive is Web Mercator (EPSG:3857), the tiling CRS, and its cell aggregates were computed before that reprojection.

## Files

One file per UTM zone at `vector/2025/zone=NN/utm{NN}.parquet` (hive-partitioned by `zone`) (e.g. [utm48](https://data.source.coop/ftw/global-data-beta/vector/2025/zone=48/utm48.parquet) is the largest, 19,610,745 parcels). Zone numbers with no land coverage are absent. Each parquet sits beside its own STAC item, and `items.parquet` mirrors every item's metadata for bulk lookup.

## Browse it

One [PMTiles archive](https://data.source.coop/ftw/global-data-beta/vector/2025/fields-2025.pmtiles) renders the whole year with a zoom handover: A5 r7 cell aggregates (`cells` layer, z0–8: `count`, `area_ha`, `avg_score`, `pct_covered`) switching to the full field polygons (`fields` layer, z9–13: `id`, `metrics:area`, `metrics:perimeter`, `score`). Four styles — count, coverage (default), avg-size, field-prob — live beside it in `styles/`; the per-cell aggregates are also published as [GeoParquet](https://data.source.coop/ftw/global-data-beta/vector/2025/cells_a5r7_2025.parquet).

## How it was made

Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics (CDSE sentinel-2-global-mosaics, 2025 Q1-Q4, 4 quarters x B02/B03/B04/B08), 2.5 m field/boundary probabilities, BoundaryVote instance post-processing (nbg-pb-h0.01-t0.3+A900), 5 m coverage simplification, parcels > 5 km2 removed. Attributes are for filtering; no land-cover masking was applied. Source imagery: the [TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Limitations

These are **model predictions**, not a survey. In the FTW project's own words, a field here is a *remote-sensing field unit* (a connected component of predicted field-interior pixels), **not** a cadastral/legal parcel, and [this is not a land-tenure product](https://source.coop/ftw/global-data); one legal parcel may map to many polygons or to none. Parcel counts, areas and perimeters are therefore predicted quantities that carry the model's errors, not measurements of anything surveyed.

- **Model provenance.** The FTW `unet_balanced_fp32.onnx` model, run on the Sentinel-2 quarterly cloudless mosaics and vectorized by BoundaryVote instance post-processing; *How it was made* above and each collection's `description` record the exact chain. The checkpoint and its model card are released by the [FTW project](https://fieldsofthe.world) separately from this data.
- **`score` is a model probability, not a validated confidence.** It is the mean field probability the model assigned to the pixels inside the parcel, × 100 and rounded into a `uint8` (0–100). Use it to rank and filter; no calibration against ground truth is published for this beta, so a score of 80 is not an 80% chance that the parcel is real.
- **Weaker outside the training distribution.** FTW describes the confidence on its earlier global release as "conservative outside the FTW training distribution (e.g. smallholder systems): real fields there may receive low confidence" ([FTW](https://source.coop/ftw/global-data)). Expect the same shape of error here, and prefer a continuous `score` over a hard threshold in smallholder regions.
- **No land-cover masking.** Nothing upstream removed non-agricultural ground, so water, scrub and built-up land can appear as parcels; `score` is the filter the dataset gives you. Parcels larger than 5 km² were dropped in post-processing.
- **Each year is an independent prediction.** `id` is unique within a year's collection and carries no meaning across years, so year-over-year comparison needs a spatial join, not an id join.

Found something wrong? Open an [issue](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).

## Fixing this metadata

`catalog/` in [the repository](https://github.com/fieldsoftheworld/ftw-global-data-catalog) **is** this catalog: it syncs 1:1 to the bucket through `tools/publish.py`, so a merged change lands here on the next publish. Publishing never deletes, and no data bytes live in git — the repository carries only the metadata that describes them.

Every file in this directory is **generated** by `tools/build_vector_items.py`. Edit that generator and re-run it; an edit to the generated output is overwritten by the next build.
