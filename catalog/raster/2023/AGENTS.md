# AGENTS.md — FTW probability rasters 2023

Guidance for AI agents. Every claim here is quoted from a verified `gdalinfo` or measured from the index manifest.

- 7,467 COGs at `https://data.source.coop/ftw/global-data-2e/raster/2023/{tile_key}.tif` (anonymous read), tile keys like `01KFS_0_0`.
- Band 1 `field`, band 2 `boundary`; uint8, probability = value / 255 (the files carry scale 1/255). 2.5 m, per-tile UTM CRS (`epsg` in the index).
- Enumerate tiles via the [index manifest](https://data.source.coop/ftw/global-data-2e/index/raster.parquet) (`year = 2023`), never by listing the bucket.
- Mean field fraction across tiles in 2023: 0.1256.
- Each COG's GDAL metadata names its four source mosaic tiles (`source_items`) and the model (`unet_balanced_fp32.onnx`).

Runnable example:

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
idx = "https://data.source.coop/ftw/global-data-2e/index/raster.parquet"
con.sql(f"""
    SELECT tile_key, href, round(field_frac, 3) AS field_frac
    FROM read_parquet('{idx}') WHERE year = 2023
    ORDER BY field_frac DESC LIMIT 5
""").show()
```
