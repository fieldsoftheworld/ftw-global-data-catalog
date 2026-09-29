# Beta PMTiles pipeline (rails)

GeoParquet → PMTiles for the beta field predictions, run on the TGI RAILS
Slurm cluster with gpio + tylertoo. One archive per year with a zoom
handover:

- **z0–8 `cells` layer** — a5 r7 aggregates, every cell verbatim.
- **z9–13 `fields` layer** — every predicted field, all parquet columns.

## Status

These scripts are the proven alpha pipeline, copied verbatim from
`fieldsoftheworld/ftw-data-catalog@fd844c4` (`scripts/tiles/`), which built
`predictions/vectors-test/fields-yearly` from the 1.58 B-feature alpha
release. They are the starting point, not yet the beta pipeline. Adapting
them to the beta source (Phase 3 of [docs/plan.md](../docs/plan.md)) changes:

- **Stage** (`stage_global.py`): read `vector/{year}/utm{NN}.parquet` from
  `ftw/global-data-beta` instead of the alpha `results-by-admin-conf`
  partitions; carry **all** parquet columns (drop only the `bbox` struct and
  constant columns from tiles); measure cross-zone duplicates before deciding
  to dedupe (beta ids are tile-scoped; the alpha `(id, area)` dedupe targeted
  a different defect); no year split (the source is already per-year); no
  `SET TimeZone` need expected (no datetime column — verify).
- **Aggregate** (`aggregate_cells.sbatch` + `add_coverage.py`): metrics from
  the beta schema — `count`, `area_ha` from `metrics:area`, `avg_field_prob`,
  `avg_boundary_prob`, `pct_covered`. The 350 km² giant-field cutoff should
  be unnecessary (beta post-processing already removes >5 km² parcels —
  verify the max first).
- **Tile/shard** (`tile_cells.sbatch`, `tile_fields.sbatch`): beta is ~12×
  smaller than alpha (134 M vs 1.58 B features for 2025), so expect hours,
  not days, possibly fewer shards, and maybe no 360 G coarse node.

The measured reference points below are from the alpha run and bound the
beta run from above.

## Running it (alpha shape, for reference)

```bash
# 1. Stage, merge, convert to GeoParquet 2.0
sbatch stage.sbatch

# 2. Cell archive
YEAR=2025 sbatch --export=ALL aggregate_cells.sbatch
YEAR=2025 sbatch --export=ALL tile_cells.sbatch     # prints tile-weight report

# 3. Field shards + handover merge
export IN=$PWD/global2025_gp2.parquet
YEAR=2025 MODE=plan   sbatch --export=ALL tile_fields.sbatch
YEAR=2025 MODE=coarse sbatch --export=ALL --mem=360G tile_fields.sbatch
for i in $(seq 0 7); do YEAR=2025 MODE=shard IDX=$i sbatch --export=ALL tile_fields.sbatch; done
YEAR=2025 MODE=merge COARSE=fields-2025-a5r7.pmtiles sbatch --export=ALL tile_fields.sbatch
```

Env vars go through the shell + `--export=ALL` (a value inside
`--export=A=x,B=y` gets comma-split by sbatch).

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

## Measured timings (beta, tylertoo main @ dabed9f, 2026-09-29)

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
for its 115.5 GB archive with a 360 G coarse node.
