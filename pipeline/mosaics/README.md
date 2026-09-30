# Quarterly Sentinel-2 mosaics

Query CDSE's public `sentinel-2-global-mosaics` STAC collection, download the
original band COGs through EODATA S3, and stack Q1–Q4 in B04/B03/B02/B08 order
(red, green, blue, NIR). Output: one 16-band 10 m GeoTIFF per complete tile.
The input reflectance values and nodata values are retained without normalization.

```sh
uv venv
uv pip install -r pipeline/mosaics/requirements.txt
.venv/bin/python pipeline/mosaics/download.py --year 2025 \
  --bbox 5 51 5.1 51.1 --output-dir stacks/2025 --scratch-dir scratch \
  --index-output index/tile_index_2025.parquet
```

Set `EODATA_S3_ACCESS_KEY` and `EODATA_S3_SECRET_KEY` in the environment.
`EODATA_S3_ENDPOINT` optionally overrides the CDSE endpoint. Obtain credentials
through [CDSE S3 access](https://documentation.dataspace.copernicus.eu/APIs/S3.html).
No credentials are written to manifests or output tags.

The bbox selects whole MGRS subtiles; it does not crop them. A global query can
use `--bbox -180 -90 180 90`, preferably with a cropland tile list supplied via
`--tile-list`. Specify disjoint `--shard` / `--num-shards` for multiple jobs.
`--workers` limits simultaneous object downloads within each tile. Scratch must
hold 16 source COGs; band stacking reads in windows to bound RAM.

Missing quarters, duplicate items, incomplete downloads and grid mismatches fail
the run. Input checksums and STAC IDs accompany the stack. Writes replace
atomically; completed stacks with the same source IDs skip on reruns. This is
input preparation, not a mirror publisher or infrastructure provisioning tool.

```sh
uv pip install pytest
.venv/bin/python -m pytest pipeline/mosaics/test_mosaics.py
```

`--index-output` writes the B04 URL index consumed by polygon QA. With shards,
write `tile_index_2025_0.parquet`, `tile_index_2025_1.parquet`, etc.; use the same
index directory for postprocessing. Source URLs retain their original access
requirements; a public mirror index can be supplied instead.
