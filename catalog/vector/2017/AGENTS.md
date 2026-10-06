# AGENTS.md — FTW field boundaries 2017

Guidance for AI agents. Every claim here is quoted from the dataset's embedded metadata or measured from the data.

- 113,556,951 parcels in 54 per-UTM-zone GeoParquet files at `https://data.source.coop/ftw/global-data-2e/vector/2017/zone=NN/utm{NN}.parquet` (anonymous read, hive-partitioned by `zone`).
- Whole-year queries glob the partitions over s3 with anonymous access and `hive_partitioning=1` (http URLs cannot glob):
  ```python
  import duckdb
  con = duckdb.connect()
  con.execute("INSTALL httpfs; LOAD httpfs; CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2', URL_STYLE 'path');")
  con.sql("SELECT zone, count(*) FROM read_parquet('s3://us-west-2.opendata.source.coop/ftw/global-data-2e/vector/2017/zone=*/utm*.parquet', hive_partitioning=1) GROUP BY zone ORDER BY zone").show()
  ```
- Schema: 9 columns (id, collection, geometry, bbox, metrics:area, metrics:perimeter, score, determination:datetime, determination:method); definitions live in `table:columns` on the collection and every item.
- Parcel ids are unique within a zone file; zones partition the parcels cleanly (measured: zero shared ids or geometries in the 6°E utm31/utm32 boundary strip).
- `metrics:area` is m². Post-processing kept parcels between 900 m² and 5 km². Inside a processed tile nothing was removed on land-cover, water or slope grounds, so non-agricultural ground can carry parcels.
- Coverage is cropland-gated: only MGRS tiles with at least 1% cropland were processed. Treat an empty region as unprocessed, not as a prediction that no fields exist there.
- **South-west Norway is missing from this year.** 2017 predates the band-V exception in the post-processing UTM-zone test, so parcels between 3°E and 6°E in the 56°N–64°N band — MGRS squares 32VKK, 32VKL, 32VLK and 32VLL, covering Bergen, Stavanger and Jæren — were rejected as outside zone 32 and are absent from the zone=31 and zone=32 files alike. The tiles were predicted and the 2017 rasters carry them; only the vectors drop them. 2018 through 2025 were rebuilt with the fix and each gained between 9,761 and 18,625 parcels there, so a year-over-year comparison in that window makes fields look as though they appeared in 2018 when the difference is only this artefact.
- Query with DuckDB over https:// URLs (s3:// hangs on some networks); a browser-like User-Agent is needed for bucket listings only, not file reads.
- The `items.parquet` collection mirror holds all item metadata for bulk spatial lookup of zones.

Runnable example:

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
