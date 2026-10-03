# AGENTS.md — FTW field boundaries 2018

Guidance for AI agents. Every claim here is quoted from the dataset's embedded metadata or measured from the data.

- 120,266,836 parcels in 54 per-UTM-zone GeoParquet files at `https://data.source.coop/ftw/global-data-2e/vector/2018/zone=NN/utm{NN}.parquet` (anonymous read, hive-partitioned by `zone`).
- Whole-year queries glob the partitions over s3 with anonymous access and `hive_partitioning=1` (http URLs cannot glob):
  ```python
  import duckdb
  con = duckdb.connect()
  con.execute("INSTALL httpfs; LOAD httpfs; CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2', URL_STYLE 'path');")
  con.sql("SELECT zone, count(*) FROM read_parquet('s3://us-west-2.opendata.source.coop/ftw/global-data-2e/vector/2018/zone=*/utm*.parquet', hive_partitioning=1) GROUP BY zone ORDER BY zone").show()
  ```
- Schema: 9 columns (id, collection, geometry, bbox, metrics:area, metrics:perimeter, score, determination:datetime, determination:method); definitions live in `table:columns` on the collection and every item.
- Parcel ids are unique within a zone file; zones partition the parcels cleanly (measured: zero shared ids or geometries in the 6°E utm31/utm32 boundary strip).
- `metrics:area` is m²; the upstream post-processing removed parcels larger than 5 km².
- Query with DuckDB over https:// URLs (s3:// hangs on some networks); a browser-like User-Agent is needed for bucket listings only, not file reads.
- The `items.parquet` collection mirror holds all item metadata for bulk spatial lookup of zones.

Runnable example:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-2e/vector/2018/zone=31/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg(score), 1) AS avg_score
    FROM read_parquet('{url}')
""").show()
```
