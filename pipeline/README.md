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

## Measured reference points (alpha global 2025, 1.58 B fields)

| step | wall |
|---|---|
| stage (598 files, one year) | ~6 h |
| coarse (z0–8 + convert plan, 360 G) | 4 h 12 m |
| 8 shards (z9–13, parallel) | 0.9–3.5 h |
| merge | 8.6 min |
| **fields archive total** | **~7 h 50 m** (115.5 GB) |
