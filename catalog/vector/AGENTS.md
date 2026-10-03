# AGENTS.md — FTW vector tree

Guidance for AI agents. Every claim here is quoted from a source or measured from the data.

- One collection per year: `2017/collection.json`, `2018/collection.json`, `2019/collection.json`, `2020/collection.json`, `2021/collection.json`, `2022/collection.json`, `2023/collection.json`, `2024/collection.json`, `2025/collection.json`.
- Each collection documents its schema in `table:columns` and its own AGENTS.md; read those before querying.
- Data layout: `vector/{year}/zone=NN/utm{NN}.parquet` — hive-partitioned by zone, each parquet colocated with its item metadata. A whole-year read globs `s3://us-west-2.opendata.source.coop/ftw/global-data-2e/vector/{year}/zone=*/utm*.parquet` with `hive_partitioning=1`; the https form cannot expand a wildcard.
- Nothing is filtered by land cover, water or slope, so non-agricultural ground can carry parcels. The only size bounds are a 900 m² floor and a 5 km² cap.
- Pipeline and catalog source: https://github.com/fieldsoftheworld/ftw-global-data-catalog
