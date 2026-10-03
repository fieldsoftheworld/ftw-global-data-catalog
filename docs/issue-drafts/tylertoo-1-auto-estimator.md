# profile auto: the sink estimate never measures property width — sample real batch bytes instead of SINK_ROW_OVERHEAD_BYTES

Milestone: v0.8 — trust

## What is wrong

`--profile auto` decides Ram-vs-Spill for the pass-2 level sinks from
`estimate_buffered_bytes` (`crates/core/src/overview/pipeline.rs:151-166`):

```
per_row = SINK_ROW_OVERHEAD_BYTES + factor(mode) × avg_geom_bytes
```

`SINK_ROW_OVERHEAD_BYTES = 4_096` is a fixed constant whose comment says it
"covers everything that is NOT input geometry: property columns (≤ ~80 B/row
on the measured corpora)" (`pipeline.rs:96-98`). The property term is never
measured, so the estimate is wrong in both directions on real catalogs:

- **Under-estimate**: wide-property datasets. A buffered row is a whole
  retained feature — geometry AND property columns as Arrow arrays
  (`pipeline.rs:72`, `LevelSink::Ram` at `:1421`). The FTW 2e vectors
  carry 17 exported properties; FIRMS-style aggregates
  (`gpio process aggregate --breakdown`) carry dozens to hundreds of
  `count_<value>` columns per feature. When real property bytes exceed the
  constant's allowance, `auto_backing` (`pipeline.rs:184`) keeps `Ram` and
  the buffered levels genuinely hold the full payload — in `duplicating`
  mode (the only mode `tiles` uses, `crates/cli/src/main.rs:2381-2387`)
  summed across every non-canonical level.
- **Over-estimate**: small-property datasets pay a 4 KiB/row assumption
  (~50× the "measured ~80 B") and spill when RAM would have been fine.

## Fix plan

1. **Measure, don't assume.** Pass 2 already reads the input in batches.
   From the first K batches (e.g. until 1M rows or 64 batches), compute the
   actual retained per-row bytes: sum of
   `Array::get_array_memory_size()` over the kept property columns plus the
   geometry column, divided by rows. Keep `SINK_ROW_OVERHEAD_BYTES` only as
   a floor for allocator/`Vec<RecordBatch>` overhead (a few hundred bytes),
   not as the property proxy.
2. **Feed the measured figure into `estimate_buffered_bytes`** and re-run
   `auto_backing`'s comparison against `AUTO_RAM_FRACTION × available RAM`
   (`pipeline.rs:184`, probe at `:243-261`,
   `TYLERTOO_AUTO_MEM_LIMIT_BYTES` override unchanged). Since the choice is
   currently made up front, either (a) delay the Ram/Spill decision until
   the sample is in — sinks start in a small Ram buffer and adopt a backing
   at the sample boundary — or (b) start with the constant-based choice and
   allow a one-way Ram→Spill downgrade when the measured rate crosses the
   budget. (b) is smaller and never loses data: flush the Ram buffer to the
   spill file at downgrade.
3. **Add the property term to the #543 preflight.** #543 wants
   `N × per_row + winner-wave budget` checked against the cgroup limit
   before the scan; the per-row figure should be the measured one (footer
   row count × sampled bytes), not the geometry-only model.
4. **Log the measured rate** in the phase stats / `TYLERTOO_PROFILE_JSON`
   so sizing regressions are visible.

## Evidence

FTW Global 2nd Edition, 134,259,417 features, 17 exported properties, tylertoo
0.11.0 @ 6b9c3ff, `tiles --shard coarse --save-plan` (full monolithic
convert, pre-#541): MaxRSS 201,321,784 K on a 192 GiB Slurm job — the run
was saved from `auto`'s mis-estimate only because we passed
`--profile bounded`. The alpha corpus (1.58 B features, ~4 small
properties) matches the constant far better, which is how the constant was
calibrated. Note the companion report on bounded-profile RSS accounting
(filed separately) for why even `bounded` rode the ceiling.

## Verification

- Unit test: a synthetic input with 200 float property columns must yield a
  measured per-row estimate ≥ 100× the geometry-only figure and flip
  `auto_backing` to `Spill` under a small
  `TYLERTOO_AUTO_MEM_LIMIT_BYTES`.
- Unit test: a 2-property input must not spill under a generous limit
  (over-estimate direction).
- Re-run the FTW 2e coarse command with `--profile auto` inside a 64 GiB
  cgroup: completes without OOM, and the profile JSON shows the measured
  per-row figure.
