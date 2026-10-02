# AGENTS.md — FTW vector tree

Guidance for AI agents. Every claim here is quoted from a source or measured from the data.

- One collection per year: `2017/collection.json`, `2018/collection.json`, `2019/collection.json`, `2020/collection.json`, `2021/collection.json`, `2022/collection.json`, `2023/collection.json`, `2024/collection.json`, `2025/collection.json`.
- Each collection documents its schema in `table:columns` and its own AGENTS.md; read those before querying.
- Data layout: `vector/{year}/zone=NN/utm{NN}.parquet` — hive-partitioned by zone, each parquet colocated with its item metadata; whole-year reads glob `zone=*/utm*.parquet`.
