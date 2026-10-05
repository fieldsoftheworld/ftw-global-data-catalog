# AGENTS.md — FTW probability rasters 2022

Guidance for AI agents. Every claim here is quoted from a verified COG header or measured from the index manifest.

Related guides: the [raster tree](../AGENTS.md), the [catalog root](../../AGENTS.md), and this collection's [README](./README.md).

- 7,466 COGs at `https://data.source.coop/ftw/global-data-2e/raster/2022/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif` (anonymous read), tile keys like `01KFS_0_0`. Each tile's directory also holds `{tile}.json`, its STAC item and `{tile}.thumb.png`.
- Band 1 `field`, band 2 `boundary`; uint8, probability = value / 255 (the files carry scale 1/255). 2.5 m, per-tile UTM CRS (79 distinct EPSG codes this year; `epsg` in the index, `proj:code` on each item). No nodata is declared.
- Enumerate tiles via the [index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet) (`year = 2022`) or the `items.parquet` mirror, never by listing the bucket. The collection carries no `rel: item` link of its own: the items hang off 54 `zone={ZZ}/catalog.json` subcatalogs, each splitting into `gzd={GZD}/catalog.json`, which carry the item links. Walking that tree costs ~400 requests per year, so for bulk work read the mirror or the manifest instead.
- Measured across 2022: mean field fraction 0.1231, max 0.9419.
- Each COG's GDAL metadata names its four source mosaic tiles (`source_items`, mirrored into each item's `ftw:source_items`) and the model (`unet_balanced_fp32.onnx`).
- Tile origins are not derivable from the tile key (measured: `01KFS_0_0` starts at (600000, 7700020), `33UUU_0_0` at (300000, 5900040)). Read `proj:transform` from the item, or the COG header.

- `https://data.source.coop/ftw/global-data-2e/raster/2022/items.parquet` mirrors every item for bulk and spatial queries.

Runnable example — the five field-densest tiles:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
idx = "https://data.source.coop/ftw/global-data-2e/index/raster.parquet"
con.sql(f"""
    SELECT tile_key, epsg, round(field_frac, 3) AS field_frac
    FROM read_parquet('{idx}') WHERE year = 2022
    ORDER BY field_frac DESC LIMIT 5
""").show()
```

Whole-collection summary from the index:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
idx = "https://data.source.coop/ftw/global-data-2e/index/raster.parquet"
con.sql(f"""
    SELECT count(*) AS tiles,
           count(DISTINCT epsg) AS utm_crs,
           round(sum(size_bytes) / 1e12, 2) AS tb,
           round(avg(field_frac), 4) AS mean_field_frac,
           round(max(field_frac), 4) AS max_field_frac,
           -- 1e10 m2 == 1 Mha; assumes tiles do not overlap
           round(sum(field_frac) * 40032 * 40032 * 6.25 / 1e10, 1)
               AS field_mha_approx
    FROM read_parquet('{idx}') WHERE year = 2022
""").show()
```
