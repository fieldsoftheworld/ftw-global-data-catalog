# Postprocessing on AWS

`launch.sh` runs `outlines.py` on a fleet of EC2 CPU boxes and then, for one year at a time, the
rest of the chain on one large box: `simplify_polygons.py`, `merge_polygons.py`,
`fiboa_ranges.py local` and `qa_vector.py`.
It is built like the inference fleet in `pipeline/inference/aws` and uses the same S3 work queue
(`pipeline/inference/aws/work_queue.py`, with `--outputs outlines`).
The 2017-2025 vectors were made this way by the production repository; the measurements below
come from that run unless they say otherwise.

## Setup

Everything account-specific comes from the environment:

| variable | what |
| --- | --- |
| `FTW_AWS_BUCKET` | a bucket in us-west-2 that holds the queue, the bundle and the outputs |
| `FTW_AWS_ROLE` | an instance profile with read and write access to that bucket |
| `FTW_AWS_SUBNETS` | `"us-west-2a=subnet-... us-west-2b=subnet-..."`, subnets with internet access |
| `FTW_INDEX_DIR` | `tile_index_{year}.parquet` from `pipeline/mosaics/indexes.py` (`bundle`, `units`) |
| `FTW_LAND_POLYGONS` | the OSM land polygons prepared by `sea_filter.py` (`setup`) |
| `SCORES` | the score COG root, `{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif` below it; default `s3://$FTW_AWS_BUCKET/infer/raster`, where the inference fleet writes |

Set `SCORES_NO_SIGN=1` when `SCORES` is a public bucket the role cannot read, for example the
published scores at `s3://us-west-2.opendata.source.coop/ftw/global-data-2e/raster`.
`PREFIX` (default `pp/`) keeps one run's keys apart from another's; every key below is
under `s3://$FTW_AWS_BUCKET/$PREFIX`.
The header of `launch.sh` lists the other variables.

The boxes run Amazon Linux 2023, install `uv`, and install `requirements.txt` into a Python 3.13
environment. `outlines.py` reads the B04 mosaics for the nodata QA from the Source Coop bucket
in us-west-2 without credentials: `bundle` adds `b04_s3_href` and `b04_s3_endpoint` for those
objects to the tile indexes (`qa_index.py`), so no Copernicus EODATA keys are needed on the
boxes. The DEM and land cover are public https reads, as everywhere else.

## Outlines

```sh
pipeline/postprocessing/aws/launch.sh setup          # check bucket, role, subnets; land polygons -> PREFIX
pipeline/postprocessing/aws/launch.sh bundle         # this commit's pipeline/postprocessing + indexes
pipeline/postprocessing/aws/launch.sh units 2025     # 256 units of about 57 tiles
pipeline/postprocessing/aws/launch.sh fleet 1100     # spot boxes over FLEET_TYPES, ~1,100 vCPUs
pipeline/postprocessing/aws/launch.sh status
pipeline/postprocessing/aws/launch.sh requeue        # after the queue is empty: units for tiles still missing
```

`bootstrap.sh` runs on each box. It starts one worker per physical core less two, capped at
`GB_PER_WORKER` (default 10) GB of RAM each, and splits them into slots of about 16 workers.
Each slot claims a unit, downloads the score COGs of the unit's tiles that have no outline in S3
yet, runs `outlines.py` on them, and an uploader moves each finished tile to
`outlines/{year}/{tile}.parquet`.
A run that fails is retried once with a quarter of the workers: one worker killed for memory
breaks the whole process pool (`pool_utils.drain`), and the tiles it had finished are kept.
A unit is marked done with the number of tiles still missing; `requeue` makes a new unit of
them.
A box stops claiming on a spot interruption notice or after `MAX_HOURS` (default 12), and
another box takes over its unit once its heartbeat is 10 minutes old.
`up N` starts N boxes of `TYPE` instead of a fleet; `down` terminates every box of the prefix.

Measured in the 2026 run (production code, which cropped each window to its foreground blocks
before BoundaryVote):

- One tile on one worker of an r7a.16xlarge: mean 24.6 min, median 17.9, p90 57, slowest 87;
  3,161 vCPU-hours for one year of 7,467 tiles. A box took 36 s from boot to its first tile.
- Per vCPU, r7a.32xlarge and r7a.48xlarge did 1.28 and 1.30 times the tiles of r7a.16xlarge;
  r7a.24xlarge 0.74, m7a.24xlarge 0.71. The default `FLEET_TYPES` leaves the 24xlarge sizes out.
- Worker memory was 7.6 GB at the median and 12.5 GB at p99, with 7 GB a worker budgeted.
- r7a.48xlarge spot alone in us-west-2 was reclaimed within 15 minutes or had no capacity; its
  spot placement score was 1 of 10 alone and 9 of 10 over several families, hence the fleet.
  In 2024, 14 of 64 units were rerun after spot reclaims.

`outlines.py` here calls BoundaryVote on whole windows. On 15TVG_0_0 (2025, a dense tile) one
worker took 22 min (43 s of it reading the B04, DEM and land cover) and peaked at 17.2 GB on the
cluster, above the production p99, which is why `GB_PER_WORKER` is 10 and not 7.
`chain.sh` then took 39 s on that one tile with 4 CPUs and peaked at 3.2 GB. Each box writes the per-tile `peak_rss_gb` and `t_total` to
`logs/{instance}/profile_*.parquet`; check the first boxes' profiles before a large fleet.

## Downstream

```sh
pipeline/postprocessing/aws/launch.sh downstream 2025
pipeline/postprocessing/aws/launch.sh status
pipeline/postprocessing/aws/launch.sh pull 2025 staging    # after downstream/2025.done is 0
```

`downstream.sh` starts one on-demand `DS_TYPE` box (default r6i.32xlarge: 128 vCPUs, 1 TiB) with
a 2 TB volume. It downloads the year's outlines with `s5cmd`, takes the expected tiles from the
year's unit files, and runs `chain.sh`. Zone files go to `fiboa/{year}/`, merge's summary to
`merged/{year}/_summary.json`, the QA report to `qa/vector_{year}.json` and the exit code to
`downstream/{year}.done`. A year whose marker is 0 is not relaunched; a failed attempt's marker
is kept as `{year}.done.failed-{time}`. The box stops after `MAX_HOURS` (default 10).
`MERGE_ARGS=--allow-missing` merges a year with tiles missing, and the fiboa footer then says
INCOMPLETE.

`chain.sh YEAR ROOT` is the same chain without AWS, on any machine:

```sh
FTW_LAND_POLYGONS=land_polygons.parquet PY=.venv/bin/python \
  pipeline/postprocessing/aws/chain.sh 2025 run   # run/outlines/2025/*.parquet, run/keep_2025.txt
```

It sizes simplify workers, DuckDB threads and memory, and `fiboa_ranges.py local --jobs` from
`CPUS` and `MEM_GB` (default: the machine's).

In the 2026 run six downstream boxes (r6i.32xlarge) took 1.9 to 2.2 h a year: download 5 min,
simplify 53 min, merge and the cross-zone seams 14 min, fiboa 46 min. That chain simplified with
a Rust port of GEOS in 16 processes of 8 threads; this one runs `coarsen` with one process per
tile, and its timing on EC2 has not been measured.

`pull` copies a finished year's zone files, summary and QA report. Publication then follows
the main README (`validate_vector.py`, `tools/hash_remote.py`, `tools/upload_data.py`).

## Cost

From the 2026 run's ledger: outlines cost $0.0254 per vCPU-hour on spot and $0.0761 on demand,
so one year (3,161 vCPU-hours on r7a.16xlarge) is about $80 on spot and $240 on demand; an
r6i.32xlarge downstream box is $8.06 an hour, about $17 a year. S3 storage and requests are
extra. The Standard spot and on-demand vCPU quotas (1,200 each on the account of that run) set
how large the fleet can be.

Compacting the raw outlines (`compact_outlines.py`) and PMTiles are not part of this directory:
compaction took 48 min a year on a 32-vCPU box and runs anywhere, and the PMTiles chain needs a `tylertoo`
build and the Slurm layout of `pipeline/run_year.sh`.
