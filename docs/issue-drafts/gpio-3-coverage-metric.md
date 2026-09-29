# aggregate: native percent-of-cell-area metric — a5 cells have a per-resolution constant area

## What is missing

"How much of each cell is covered" is the single most useful choropleth for
polygon layers aggregated to a grid, and every catalog reimplements it
downstream. The FTW catalogs compute
`pct_covered = 100 * sum(metrics:area) / cell_area` in a post-pass
([`add_coverage.py`](https://github.com/fieldsoftheworld/ftw-global-data-catalog/blob/main/pipeline/add_coverage.py))
that also has to re-measure the cell area geodesically because DuckDB's
`ST_Area_Spheroid` returns NaN or wildly wrong values on a5 cell polygons.
gpio can compute this itself in the aggregate: a5 is an equal-area grid, so
the denominator is one constant per resolution (measured r7 ≈ 2,075.5 km²,
sample spread < 0.1%).

Today no cell-area or coverage metric exists: `--metric` parses only
`func:column` with `VALID_METRIC_FUNCS = {"sum","avg","min","max"}`
(`core/process/aggregate/common.py:14`, `parse_metrics` at `:96-125`), and
`validate_agg_columns` (`common.py:293-328`) requires every metric column
to exist in the input schema.

## Fix plan

1. **New metric kind** `pct_cell:column` — column holds per-feature area in
   m²; output column `pct_<column>` (sanitized) =
   `100 * SUM(column) / cell_area_m2(resolution)`, rounded by the existing
   type plumbing.
   - `parse_metrics` (`common.py:96-125`): accept `pct_cell` in
     `VALID_METRIC_FUNCS`; keep `MetricSpec(func="pct_cell", column, output_name)`.
   - `build_metric_select` (`common.py:234-263`): branch for `pct_cell` that
     emits `100.0 * SUM(col) / {cell_area}` instead of the `FUNC(col)`
     template. The resolution is already in scope in `build_grid_query`
     (`grid_common.py:273-325`); thread it into the metric builder.
   - `validate_agg_columns` keeps validating `column` normally (it is a real
     input column), so no schema-check change is needed for this variant.
2. **Cell area source.** Prefer an exact function from the a5 extension if
   it exposes one (check `a5_cell_area`/similar in the Query Farm build the
   package already loads, `core/duckdb_utils.py:676-701`). Otherwise ship a
   per-resolution constant table derived analytically (a5 is equal-area:
   earth surface area / cell count at resolution), with the r5/r7/r8 values
   cross-checked against geodesic measurement (r5 ≈ 33,208 km²,
   r7 ≈ 2,075.5 km², r8 ≈ 518.9 km²). Do not use `ST_Area_Spheroid` (broken
   on these polygons, see above). h3 is not equal-area; either compute the
   per-cell area exactly via the h3 extension's area function or restrict
   `pct_cell` to a5 initially with a clear error.
3. **Overview rollup.** In `build_rollup_agg_parts`
   (`overview/rollup.py:46-66`), a parent's `pct_<col>` is
   `100 * SUM(child_sum_col) / parent_cell_area`. That needs the underlying
   sum: when `pct_cell:x` is requested, also emit (or require) `sum_x`, and
   have rollup recompute `pct_x` from the rolled-up sum at the parent
   resolution rather than averaging child percentages.
4. **Docs**: note the "assigned wholly to home cell" semantics — a boundary
   feature contributes its full area to one cell, so values can slightly
   exceed 100.

### Acceptance

- `--metric "sum:metrics:area,pct_cell:metrics:area" --resolution 7` on the
  FTW beta 2025 input reproduces the downstream `pct_covered` values
  (within rounding) with `add_coverage.py`'s coverage step deleted.
- `process overview` parents carry recomputed `pct_*` consistent with their
  own resolution's cell area.
- `pct_cell` on an h3 run either works with exact per-cell areas or fails
  with a clear message (whichever variant is chosen).
