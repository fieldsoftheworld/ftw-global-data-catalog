# AGENTS.md — FTW vector tree

Guidance for AI agents. Every claim here is quoted from a source or measured from the data.

Around this file: [catalog.json](./catalog.json) is the normative metadata, [README.md](./README.md) is the human landing page, and [../AGENTS.md](../AGENTS.md) covers the whole catalog (vector and raster).

## The tree

- One collection per year, each with its own agent guide: [2020](./2020/AGENTS.md), [2024](./2024/AGENTS.md), [2025](./2025/AGENTS.md).
- Every year shares one schema (9 columns, documented in `table:columns` on each collection and item), one layout and one set of styles, so a query written against one year runs against any of them. Read the year's AGENTS.md for its measured numbers.
- Data layout: `vector/{year}/zone=NN/utm{NN}.parquet` — hive-partitioned by zone, each parquet colocated with its item metadata; whole-year reads glob `zone=*/utm*.parquet` over `s3://` with `hive_partitioning=1` (http URLs cannot glob). The `zone` key comes back as a zero-padded string.
- `geometry` is WGS 84 lon/lat (EPSG:4326) in every zone file, so cross-zone and cross-year reads need no reprojection; the consequence is that `ST_Area` on `geometry` returns square degrees — use `metrics:area` (m²). PMTiles are Web Mercator (EPSG:3857).
- Years are independent predictions: `id` is not stable across them, so year-over-year comparison needs a spatial join.
- These are *remote-sensing field units*, **not** cadastral parcels; [this is not a land-tenure product](https://source.coop/ftw/global-data). Each collection's README has the limitations in full.

## Fixing this metadata

`catalog/` in [the repository](https://github.com/fieldsoftheworld/ftw-global-data-catalog) **is** this catalog: it syncs 1:1 to the bucket through `tools/publish.py`, so a merged change lands here on the next publish. Publishing never deletes, and no data bytes live in git — the repository carries only the metadata that describes them.

Every file in this directory is **generated** by `tools/build_vector_items.py`. Edit that generator and re-run it; an edit to the generated output is overwritten by the next build.
