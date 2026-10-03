# 2e PMTiles pipeline (rails)

GeoParquet → PMTiles for the FTW 2nd Edition (2e) field predictions, run on the TGI RAILS
Slurm cluster with gpio + tylertoo. One archive per year with a zoom
handover:

- **z0–8 `cells` layer** — a5 r7 aggregates, every cell verbatim.
- **z9–13 `fields` layer** — every predicted field, all parquet columns.

## Status

This is the pipeline that built the 2e per-year archives (`vector/{year}/fields-{year}.pmtiles`
and `cells_a5r7_{year}.parquet`, 2017-2025). It descends from the alpha pipeline
(`fieldsoftheworld/ftw-data-catalog@fd844c4`, `scripts/tiles/`, which built
`predictions/vectors-test/fields-yearly` from 1.58 B features) and differs from it for 2e:

- **Stage** (`stage_global.py`): reads the 54 `vector/{year}/zone=NN/utm{NN}.parquet` zone files
  (public proxy by default; `SRC_ROOT=/path/to/hive/tree` reads a local copy of the same bytes)
  and carries **all** columns except the redundant `bbox` struct. No dedupe (zones partition
  parcels cleanly, measured), no year split (the source is per-year), `TimeZone='UTC'` so
  `determination:datetime` cannot shift.
- **Aggregate** (`aggregate_cells.sbatch` + `add_coverage.py`): `count`, `area_ha` from
  `metrics:area`, `avg_score` and `pct_covered`. No giant-field cutoff: upstream already removes
  parcels > 5 km² (measured max 5.007 km²).
- **Tile/shard** (`tile_cells.sbatch`, `tile_fields.sbatch`): 2e is ~12× smaller than alpha
  (134 M vs 1.58 B features for 2025), so a year takes under two hours of compute and the
  coarse step is `--plan-only` (no 192-360 G node).
- **Upload** (`upload_year.sbatch` + `s3_put_retry.py`): per-part retry through the Source
  Cooperative proxy, then size + multihash records for the collection assets
  (`tilecheck_remote.py` samples tiles from the uploaded archive and compares feature counts with
  the zone files).

## Running it

```bash
# one year, whole chain with Slurm dependencies (sizes overridable via *_C / *_M env)
./run_year.sh 2025

# or step by step
sbatch stage.sbatch                                   # stage + merge to GeoParquet 2.0
YEAR=2025 sbatch --export=ALL aggregate_cells.sbatch
YEAR=2025 sbatch --export=ALL tile_cells.sbatch       # prints tile-weight report
export IN=$PWD/global2025_gp2.parquet N=4             # N shards; run_year.sh defaults to 4
YEAR=2025 MODE=plan   sbatch --export=ALL tile_fields.sbatch
YEAR=2025 MODE=coarse sbatch --export=ALL tile_fields.sbatch
for i in $(seq 0 3); do YEAR=2025 MODE=shard IDX=$i sbatch --export=ALL tile_fields.sbatch; done
YEAR=2025 MODE=merge COARSE=fields-2025-a5r7.pmtiles sbatch --export=ALL tile_fields.sbatch
YEAR=2025 sbatch --export=ALL upload_year.sbatch      # optional: PY=/path/to/python PROFILE=name
```

Env vars go through the shell + `--export=ALL` (a value inside `--export=A=x,B=y` gets
comma-split by sbatch).

## Cluster gotchas (all learned the hard way on alpha)

- `/tmp` is tmpfs and counts against the job cgroup — always point `TMPDIR`
  at `/u` (the scripts do).
- DuckDB `s3://` URLs hang on compute nodes (blackholed EC2 metadata probe);
  use `https://data.source.coop/...`, and set a browser-like User-Agent for
  the list API (it 403s python-urllib).
- A plain DuckDB `COPY` of GeoParquet drops the `geo` metadata key — merge
  with `gpio extract`, and follow any DuckDB rewrite with
  `gpio convert geoparquet ... --geoparquet-version 2.0`.
- tylertoo must be built ON the cluster (glibc 2.28): clone to
  `~/tylertoo-src`, `PROTOC=/u/cholmes/micromamba/envs/ftw/bin/protoc cargo
  build --release`.
- GeoParquet 2.0 conversion is what enables tylertoo's row-group pruning
  (native geo stats) — shard jobs then read ~2% of row groups instead of the
  whole file.
- a5 is equal-area: r7 ≈ 2,075.5 km² per cell. DuckDB's `ST_Area_Spheroid`
  is broken on a5 cell polygons (NaN or 100× off) — `add_coverage.py`
  self-calibrates with pyproj. Dateline cells have vertices past ±180 and
  must be wrapped or tile exporters drop them.

## Uploading through the Source Cooperative proxy

Quirks of `data.source.coop` seen while uploading the 2e products and mirroring the mosaics:

- Single PUTs of about 170 MB and up answer 413. Use multipart with 16 MiB parts (aws-cli
  `multipart_chunksize = 16MB`); `s3_put_retry.py` does.
- A part can answer 520. botocore does not retry it and `aws s3 cp` then restarts the whole file,
  so a large file can fail every pass. `s3_put_retry.py` retries the failed part only (10 times,
  exponential backoff) and checks the remote size afterwards.
- Credentials are per-session STS keys that only work against the proxy
  (`AWS_PROFILE=source-coop AWS_ENDPOINT_URL=https://data.source.coop`), not the bucket directly.
- rclone fails with `SignatureDoesNotMatch` on `CreateMultipartUpload` (unresolved); aws-cli and
  boto3 are fine.
- Reads: the proxy 403s Python's default `Python-urllib` User-Agent, so send a browser-like one.
  CDSE STAC search (the mosaic side, not the proxy) answers 429 under concurrent enumerations;
  enumerate serially with jittered backoff.
- `publish.py` / `upload_data.py` never delete, and a dry run lists the prefix directory by
  directory, never recursively.

## Measured timings (2e, tylertoo main @ dabed9f, 2026-09-29)

Wall time per step (Slurm sacct; queue waits excluded). The coarse step is
`--plan-only` (tylertoo #541/#574): it writes only the convert plan, so it
needs neither the 192–360 G of the old full-convert coarse nor its hours.

| step | 2024 (120.5 M) | 2025 (134.3 M) |
|---|---|---|
| stage (54 zones → GP2) | 51 m | 1 h 03 m |
| a5 r7 aggregate | 41 s | 40 s |
| cells archive z0–8 | seconds | seconds |
| shard plan | 1 s | 1 s |
| plan-only coarse | 6 m 31 s | 4 m 19 s (MaxRSS 22.6 GiB) |
| 4 shards z9–13 (parallel) | 12–24 m | 13–25 m |
| handover merge | 2 m 33 s | 3 m 57 s |
| **compute wall, stage→archive** | **≈ 1 h 25 m** (27.6 GB) | **≈ 1 h 35 m** (29.1 GB) |

Reference: the same 2025 build on tylertoo 6b9c3ff (pre plan-only, 17-column
schema) took 37 m 44 s at 192 GiB MaxRSS for the coarse step alone and
produced a 44.1 GB archive. The alpha run (1.58 B features) took ~7 h 50 m
for its 115.5 GB archive with a 360 G coarse node. The alpha figures bound the 2e run from above.

## Mosaic input preparation

[Quarterly mosaic downloads](mosaics/README.md) assemble 16-band input tiles.

## Model inference

[Quarterly-mosaic inference](inference/README.md) produces probability COGs.

## Polygon postprocessing

[Parcel outline and release pipeline](postprocessing/README.md) uses the external fbp package.
