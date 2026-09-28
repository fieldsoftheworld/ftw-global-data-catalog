# AGENTS.md — FTW vector tree

Guidance for AI agents. Every claim here is quoted from a source or measured from the data.

- One collection per year: `2024/collection.json`, `2025/collection.json`.
- Each collection documents its schema in `table:columns` and its own AGENTS.md; read those before querying.
- Data layout: `vector/{year}/utm{NN}.parquet`, one GeoParquet per UTM zone, colocated with the item metadata.
