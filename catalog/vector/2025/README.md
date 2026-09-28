# FTW Global — Field Boundaries 2025 (GeoParquet)

Predicted agricultural field boundaries for 2025: **134,259,417 parcels** in 54 per-UTM-zone GeoParquet files (116.0 GiB). Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Browse it in the [data browser](https://source.coop/ftw/global-data-beta).

## How it was made

Fields of The World (FTW) model on Sentinel-2 quarterly cloudless mosaics (CDSE sentinel-2-global-mosaics, 2025 Q1-Q4, 4 quarters x B02/B03/B04/B08), 2.5 m field/boundary probabilities, BoundaryVote instance post-processing (nbg-pb-h0.01-t0.3+A900), 5 m coverage simplification, parcels > 5 km2 removed. Attributes are for filtering; no land-cover masking was applied. Source imagery: the [TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/).

## Files

One file per UTM zone at `vector/2025/utm{NN}.parquet` (e.g. [utm48](https://data.source.coop/ftw/global-data-beta/vector/2025/utm48.parquet) is the largest, 19,617,748 parcels). Zone numbers with no land coverage are absent.

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
| `ftw:tile` | Sentinel-2 MGRS tile the parcel was extracted from. |
| `ftw:field_prob` | Mean model field probability inside the parcel. |
| `ftw:boundary_prob` | Mean model boundary probability inside the parcel. |
| `ftw:frac_nodata_1q` | Fraction of the parcel with nodata in >= 1 of the 4 quarterly mosaics. |
| `ftw:frac_nodata_3q` | Fraction with nodata in >= 3 quarterly mosaics. |
| `ftw:frac_water` | Fraction classed water in io-lulc 2024 (30 m). |
| `ftw:frac_crops_ever` | Fraction classed crops in io-lulc 2017, 2020 or 2024. |
| `ftw:slope_mean` | Mean slope, degrees (Copernicus 30 m DEM). |
| `ftw:frac_slope_gt30` | Fraction with slope > 30 degrees. |
| `ftw:elev_mean` | Mean elevation, m. |
| `ftw:patch_sat_mean` | Mean over covering inference patches of the fraction of pixels with field prob >= 0.9 (flipped-patch detector). |
| `ftw:patch_sat_max` | Max over covering inference patches of that fraction. |
| `ftw:patch_pb_mean` | Mean boundary probability over the covering inference patches. |
| `ftw:touches_window_edge` | Parcel touched a processing window edge (possible truncation). |

## Query it

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-beta/vector/2025/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg("ftw:field_prob"), 3) AS avg_field_prob
    FROM read_parquet('{url}')
""").show()
```

The attributes are for filtering: no land-cover masking was applied upstream, so filter on `ftw:field_prob`, `ftw:frac_water`, `ftw:frac_crops_ever` etc. to taste.
