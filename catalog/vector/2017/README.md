# FTW Global — Field Boundaries 2017 (GeoParquet)

Predicted agricultural field boundaries for 2017: **113,556,951 parcels** in 54 per-UTM-zone GeoParquet files (72.7 GiB). Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

**[Open 2017 on the interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/#year=2017)** to see the fields over imagery, or **[open it in the Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/vector/2017/collection.json)** to walk the metadata and preview each asset. The files themselves are listed on [Source Cooperative](https://source.coop/ftw/global-data-2e).

Agents: [AGENTS.md](./AGENTS.md) beside this file is the agent guide.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## How it was made

Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics (CDSE sentinel-2-global-mosaics, 2017 Q1-Q4, 4 quarters x B02/B03/B04/B08), 2.5 m field/boundary probabilities, BoundaryVote instance post-processing (nbg-pb-h0.01-t0.3+A900), 5 m coverage simplification, parcels > 5 km2 removed. No parcel is removed on land-cover, water or terrain grounds: the retention test is UTM-zone and MGRS-square ownership plus the size bounds above. Source imagery: the [TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/). [pipeline/README.md](https://github.com/fieldsoftheworld/ftw-global-data-catalog/blob/main/pipeline/README.md) documents every stage, from mosaic download to this file.

## Files

One file per UTM zone at `vector/2017/zone=NN/utm{NN}.parquet`, hive-partitioned by `zone`. The largest is [utm48](https://data.source.coop/ftw/global-data-2e/vector/2017/zone=48/utm48.parquet), with 18,298,288 parcels. Zone numbers with no land coverage are absent.

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

One [PMTiles archive](https://data.source.coop/ftw/global-data-2e/vector/2017/fields-2017.pmtiles) renders the whole year with a zoom handover: A5 r7 cell aggregates (`cells` layer, z0–8: `count`, `area_ha`, `avg_score`, `pct_covered`) switching to the full field polygons (`fields` layer, z9–13: `id`, `metrics:area`, `metrics:perimeter`, `score`). Four styles — count, coverage (default), avg-size, field-prob — live beside it in `styles/`; the per-cell aggregates are also published as [GeoParquet](https://data.source.coop/ftw/global-data-2e/vector/2017/cells_a5r7_2017.parquet).

## Query it

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-2e/vector/2017/zone=31/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg(score), 1) AS avg_score
    FROM read_parquet('{url}')
""").show()
```

Read the whole year at once by globbing the partitions over `s3://` with `hive_partitioning=1`; an HTTP URL cannot expand a wildcard. The collection's `data` asset carries both forms.

Nothing is filtered out by land cover, so water, scrub and built-up ground can carry predicted parcels. Filter on `score` (the model's field probability × 100) to trade precision against recall.
