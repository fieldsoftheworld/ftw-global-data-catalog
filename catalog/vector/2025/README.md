# FTW Global — Field Boundaries 2025 (GeoParquet)

Predicted agricultural field boundaries for 2025: **134,085,099 parcels** in 54 per-UTM-zone GeoParquet files (89.1 GiB). Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Browse it in the [data browser](https://source.coop/ftw/global-data-2e).

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## How it was made

Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics (CDSE sentinel-2-global-mosaics, 2025 Q1-Q4, 4 quarters x B02/B03/B04/B08), 2.5 m field/boundary probabilities, BoundaryVote instance post-processing (nbg-pb-h0.01-t0.3+A900), 5 m coverage simplification, parcels > 5 km2 removed. Attributes are for filtering; no land-cover masking was applied. Source imagery: the [TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Files

One file per UTM zone at `vector/2025/zone=NN/utm{NN}.parquet` (hive-partitioned by `zone`) (e.g. [utm48](https://data.source.coop/ftw/global-data-2e/vector/2025/utm48.parquet) is the largest, 19,611,921 parcels). Zone numbers with no land coverage are absent.

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

## Browse it

One [PMTiles archive](https://data.source.coop/ftw/global-data-2e/vector/2025/fields-2025.pmtiles) renders the whole year with a zoom handover: A5 r7 cell aggregates (`cells` layer, z0–8: `count`, `area_ha`, `avg_score`, `pct_covered`) switching to the full field polygons (`fields` layer, z9–13: `id`, `metrics:area`, `metrics:perimeter`, `score`). Four styles — count, coverage (default), avg-size, field-prob — live beside it in `styles/`; the per-cell aggregates are also published as [GeoParquet](https://data.source.coop/ftw/global-data-2e/vector/2025/cells_a5r7_2025.parquet).

## Query it

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-2e/vector/2025/zone=31/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg(score), 1) AS avg_score
    FROM read_parquet('{url}')
""").show()
```

No land-cover masking was applied upstream: filter on `score` (the model's field probability × 100) to trade precision against recall.
