# process aggregate: cap and narrow the DuckDB scan — 115 GiB RSS to aggregate 42 GB into 37k cells

## What happened

`gpio process aggregate a5` used 115.2 GiB MaxRSS to aggregate a 42 GB /
134M-row GeoParquet into 37,206 r7 cells. The job succeeds only because the
cluster node had 192 GiB. The same command on a laptop dies.

```
gpio process aggregate a5 global2025_gp2.parquet cells_raw.parquet \
  --resolution 7 --metric "sum:metrics:area,avg:score" \
  --out-geometry polygon --geoparquet-version 2.0
# Slurm job 205790: Elapsed 00:00:45, MaxRSS 115,190,196 K, 48 cores
# input: ftw/global-data-2e vector/2025 merged (134,259,417 rows, 42 GB)
```

No `--breakdown` was used, so the known-heavy breakdown materialization is
not the cause here. Two code-level causes:

1. **The connection is uncapped.** `aggregate_grid_file`
   (`core/process/aggregate/grid_common.py:586`, also `:687`,
   `by_admin.py:246`, `overview/detect.py:292`, `overview/rollup.py:187`)
   calls `get_duckdb_connection(load_spatial=True, load_httpfs=True)` with
   no `memory_limit`, `threads`, `temp_directory`, or
   `preserve_insertion_order` — `get_duckdb_connection`
   (`core/duckdb_utils.py:550-647`) only sets them when passed. DuckDB
   grabs its default fraction of node RAM and never spills to a
   caller-chosen directory.
2. **The scan is `SELECT *`.** `read_grid_source_sql`
   (`grid_common.py:238-270`) reads every column including the full
   geometry, when the aggregate needs only the bucketing point and the
   metric/breakdown columns.

## Fix plan

### 1. Route the aggregate/overview connections through the #1154 funnel

This is another instance of the family #1154 → #1156/#1166 → #1174 have
been closing: DuckDB work running at its own default (80% of host RAM,
blind to a Slurm job cgroup). #1154 already built the cgroup-aware limit
(50% of the process ceiling, threads capped) for writes — reuse that same
helper here rather than inventing a second default:

- Pass `memory_limit`/`threads`/`temp_directory` (from the #1154 sizing
  helper) into `get_duckdb_connection` at the five aggregate/overview call
  sites listed above. Default `temp_directory` to a `.gpio-spill/` dir
  beside the output, cleaned on success.
- Set `preserve_insertion_order = false` on these connections (already
  done elsewhere: `core/add/admin_divisions.py:469`,
  `core/partition/staging.py:135`).
- Surface `--memory-limit`/`--threads` overrides in the shared
  `grid_aggregate_options` decorator (`cli/decorators.py:505` area) and the
  admin command, consistent with whatever flag shape #1174 settles on.

### 2. Project only the needed columns

In `read_grid_source_sql` (`grid_common.py:238-270`), replace `SELECT *`
with an explicit projection: the keying point source (geometry, or the bbox
struct when `--bucket-point bbox`), each `MetricSpec.column`, the
`--breakdown` column, and any `--where` referenced columns (parse or, more
simply, keep `SELECT *` only when `--where` is present and document it).
With `--bucket-point bbox` the geometry column is never read at all — for
the dataset above that turns a 42 GB scan into a low-single-digit-GB scan.

### 3. Narrow the breakdown materialization

`build_grid_query` (`grid_common.py:302-313`) materializes
`CREATE TEMP TABLE __agg_keyed AS <all columns + __pt + __key>`. Project it
to `(__key, breakdown_col, metric cols)` — that is all
`resolve_breakdown_values` (`common.py:374-386`) and the outer aggregate
read.

### Acceptance

- The command above, run inside a 16 GiB cgroup with default flags,
  completes with identical output (byte-compare the sorted cell table).
- MaxRSS for the 134M-row case drops below ~16 GiB.
- A `--breakdown` run on the same input completes inside the same cap.
- Existing aggregate/overview tests pass unchanged.
