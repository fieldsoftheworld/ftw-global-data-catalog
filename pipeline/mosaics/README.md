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

The bbox selects whole MGRS subtiles; it does not crop them. It is checked as a
geographic `W S E N` box — `W < E`, `S < N`, `|lat| <= 90`, `|lon| <= 180` — so a
swapped pair errors instead of querying somewhere else; pass
`--allow-antimeridian` for the `W > E` box that crosses 180. A global query can
use `--bbox -180 -90 180 90`, preferably with a cropland tile list supplied via
`--tile-list`, which is then also the intended tile set: tiles it names that the
STAC query does not return fail the run. Specify disjoint `--shard` /
`--num-shards` for multiple jobs.
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

`--index-output` writes the B04 source index consumed by polygon QA, one row per
tile and quarter:

| column | meaning |
| --- | --- |
| `tile_key` | MGRS sub-tile key, e.g. `31UFS_0_0` |
| `quarter` | `Q1`–`Q4` |
| `b04_s3_href` | `s3://<bucket>/<key>` — **the path to open.** The same EODATA S3 object this pipeline reads: `EODATA_S3_ACCESS_KEY` / `EODATA_S3_SECRET_KEY`, path-style addressing, endpoint in `b04_s3_endpoint`. GDAL opens it as `/vsis3/<bucket>/<key>` with `AWS_S3_ENDPOINT`, `AWS_VIRTUAL_HOSTING=FALSE` and the same keys |
| `b04_s3_endpoint` | the endpoint the row was written against (`EODATA_S3_ENDPOINT` when set) |
| `b04_odata_href` | the item's CDSE OData alternate, **provenance only** — it needs an OIDC bearer token and GDAL's extension check rejects its `/$value` path, so it is not an open path. Empty when the item has no alternate |

Each shard
holds only its own tiles, so with `--num-shards N` the path gains a
`.shard-K-of-N` suffix: `--index-output index/tile_index_2025.parquet --shard 1
--num-shards 4` writes `index/tile_index_2025.shard-1-of-4.parquet`. No shard
can overwrite another's rows, and postprocessing merges the parts once every
shard has finished:

```sh
.venv/bin/python - <<'PY'
import pathlib, pyarrow as pa, pyarrow.parquet as pq
parts = sorted(pathlib.Path("index").glob("tile_index_2025.shard-*-of-*.parquet"))
pq.write_table(pa.concat_tables([pq.read_table(p) for p in parts]),
               "index/tile_index_2025.parquet", compression="zstd")
PY
```

A single-shard run (the default) writes the given path unchanged. Source URLs
retain their original access requirements; a public mirror index can be supplied
instead.
