# AGENTS.md — FTW fields-yearly (PMTiles)

Guidance for AI agents. Every claim here is measured from the data or quoted from the pipeline.

- PMTiles per year at `https://data.source.coop/ftw/global-data-beta/vector/fields-yearly/fields-{year}.pmtiles` with layers `cells` (z0–8) and `fields` (z9–13).
- For analysis prefer the GeoParquet: per-cell aggregates at `https://data.source.coop/ftw/global-data-beta/vector/fields-yearly/cells_a5r7_{year}.parquet`, source polygons in the per-year collections at `https://data.source.coop/ftw/global-data-beta/vector/{year}/`.
- `pct_covered` can slightly exceed 100: a parcel is assigned wholly to one cell, so boundary parcels contribute their full area there.

Runnable example:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-beta/vector/fields-yearly/cells_a5r7_2025.parquet"
con.sql(f"""
    SELECT count(*) AS cells, sum(count) AS parcels,
           round(avg(pct_covered), 2) AS mean_pct
    FROM read_parquet('{url}')
""").show()
```
