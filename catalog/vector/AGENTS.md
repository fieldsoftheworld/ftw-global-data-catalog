# AGENTS.md — FTW vector tree

The 2018, 2020, 2021, 2022, 2023, 2024 and 2025 collections use the updated processing documented in their year collections. Other year links retain historical published collections; they do not imply completion of the updated recipe for those years. Consult each collection for its provenance, footprint and asset inventory.

Guidance for AI agents. Every claim here is quoted from a source or measured from the data.

- One collection per year: `2017/collection.json`, `2018/collection.json`, `2019/collection.json`, `2020/collection.json`, `2021/collection.json`, `2022/collection.json`, `2023/collection.json`, `2024/collection.json`, `2025/collection.json`.
- Each collection documents its schema in `table:columns` and its own AGENTS.md; read those before querying.
- Data layout: `vector/{year}/zone=NN/utm{NN}.parquet` — hive-partitioned by zone, data files use zone=NN while JSON items use vector/{year}/utmNN/utmNN.json. A whole-year read globs `s3://us-west-2.opendata.source.coop/ftw/global-data-2e/vector/{year}/zone=*/utm*.parquet` with `hive_partitioning=1`; the https form cannot expand a wildcard.
- For 2018, 2020, 2021, 2022, 2023, 2024 and 2025, extraction uses a minimum mean field probability of 0.10, fills enclosed holes strictly smaller than 100 m², and uses a minimum area of 900 m². Independent crop filtering evaluates polygons with finite area from 900 through 5,000,000 m² against the union of native cropland observations from 2017, 2020 and 2024, removing polygons with less than 5% overlap. Unknown or incomplete coverage, nonfinite areas and polygons outside that size interval are retained by this filtering stage. Downstream vector cleanup is documented separately in each vector collection; its 20 m² hole-cleanup description is distinct from extraction hole filling.
- Pipeline and catalog source: https://github.com/fieldsoftheworld/ftw-global-data-catalog
