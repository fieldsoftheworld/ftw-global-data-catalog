# AGENTS.md — FTW field boundaries 2024

Guidance for AI agents. Every claim here is quoted from the dataset's embedded metadata or measured from the data.

- 120,455,491 parcels in 54 per-UTM-zone GeoParquet files at `https://data.source.coop/ftw/global-data-beta/vector/2024/utm{NN}.parquet` (anonymous read).
- Schema: 20 columns (id, collection, geometry, bbox, metrics:area, metrics:perimeter, ftw:tile, ftw:field_prob, ftw:boundary_prob, ftw:frac_nodata_1q, ftw:frac_nodata_3q, ftw:frac_water, ftw:frac_crops_ever, ftw:slope_mean, ftw:frac_slope_gt30, ftw:elev_mean, ftw:patch_sat_mean, ftw:patch_sat_max, ftw:patch_pb_mean, ftw:touches_window_edge); definitions live in `table:columns` on the collection and every item.
- Parcel ids are unique within a zone file; parcels on zone boundaries can appear in more than one file — check `ftw:touches_window_edge` before cross-zone aggregation.
- `metrics:area` is m²; the upstream post-processing removed parcels larger than 5 km².
- Query with DuckDB over https:// URLs (s3:// hangs on some networks); a browser-like User-Agent is needed for bucket listings only, not file reads.
- The `items.parquet` collection mirror holds all item metadata for bulk spatial lookup of zones.

Runnable example:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-beta/vector/2024/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg("ftw:field_prob"), 3) AS avg_field_prob
    FROM read_parquet('{url}')
""").show()
```
