# AGENTS.md — FTW field boundaries 2020

Guidance for AI agents. Every claim here is quoted from the dataset's embedded metadata or measured from the data.

Around this file: [collection.json](./collection.json) is the normative metadata, [README.md](./README.md) carries the schema table and the limitations in full, and [../AGENTS.md](../AGENTS.md) covers the vector tree this year sits in.

## Access

- 129,270,485 parcels in 54 per-UTM-zone GeoParquet files at `https://data.source.coop/ftw/global-data-beta/vector/2020/zone=NN/utm{NN}.parquet` (anonymous read, hive-partitioned by `zone`).
- Read in place over https:// URLs — the files stream over HTTP range requests, so there is no reason to download them. Use https:// rather than s3:// for single files (s3:// hangs on some networks); a browser-like User-Agent is needed for bucket listings only, not for file reads.
- Whole-year queries glob the partitions over s3 with anonymous access and `hive_partitioning=1` (http URLs cannot glob):
  ```python
  import duckdb
  con = duckdb.connect()
  con.execute("INSTALL httpfs; LOAD httpfs; CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2', URL_STYLE 'path');")
  con.sql("SELECT zone, count(*) FROM read_parquet('s3://us-west-2.opendata.source.coop/ftw/global-data-beta/vector/2020/zone=*/utm*.parquet', hive_partitioning=1) GROUP BY zone ORDER BY zone").show()
  ```
- `zone` from the partition path is a **string**, zero-padded: `WHERE zone = '31'`, not `zone = 31`.
- The `items.parquet` collection mirror holds all item metadata for bulk spatial lookup of zones.

## Schema and CRS

- 9 columns (id, collection, geometry, bbox, metrics:area, metrics:perimeter, score, determination:datetime, determination:method); definitions live in `table:columns` on the collection and every item.
- `geometry` is WGS 84 lon/lat (EPSG:4326) in **every** zone file — the UTM zone is a partition key, not a CRS, so cross-zone reads need no reprojection. The consequence: `ST_Area`/`ST_Length` on `geometry` return degree-based numbers, so read `metrics:area` (m², already computed) and `metrics:perimeter` (m) instead, or reproject to an equal-area CRS first.
- The PMTiles archive is Web Mercator (EPSG:3857); the GeoParquet is not.
- Parcel ids are unique within a zone file; zones partition the parcels cleanly (measured: zero shared ids or geometries in the 6°E utm31/utm32 boundary strip). Ids carry no meaning across years — each year is an independent prediction, so year-over-year work needs a spatial join.
- `metrics:area` is m²; the upstream post-processing removed parcels larger than 5 km².

## What this data is not

- A parcel is a *remote-sensing field unit*, **not** a cadastral/legal parcel; [this is not a land-tenure product](https://source.coop/ftw/global-data). Do not answer ownership, tenure or legal-boundary questions from it.
- `score` is the mean model field probability inside the parcel (× 100, `uint8` 0–100) — a ranking for filtering, not a calibrated probability that the parcel is real, and no calibration is published for this beta.
- No land-cover masking was applied upstream, so water, scrub and built-up ground can appear as parcels. Counts and areas are predictions; say so when you report them.
- The full set of caveats, with sources, is in [README.md](./README.md#limitations).

## Runnable example

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
url = "https://data.source.coop/ftw/global-data-beta/vector/2020/zone=31/utm31.parquet"
con.sql(f"""
    SELECT count(*) AS parcels,
           round(sum("metrics:area") / 1e6, 1) AS km2,
           round(avg(score), 1) AS avg_score
    FROM read_parquet('{url}')
""").show()
```

## Fixing this metadata

`catalog/` in [the repository](https://github.com/fieldsoftheworld/ftw-global-data-catalog) **is** this catalog: it syncs 1:1 to the bucket through `tools/publish.py`, so a merged change lands here on the next publish. Publishing never deletes, and no data bytes live in git — the repository carries only the metadata that describes them.

Every file in this directory is **generated** by `tools/build_vector_items.py`. Edit that generator and re-run it; an edit to the generated output is overwritten by the next build.
