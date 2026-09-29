# aggregate/overview: a5 cell polygons crossing ±180 have out-of-range vertices — tile exporters drop them

## What happened

A5 cells that straddle the antimeridian come out of
`gpio process aggregate a5 --out-geometry polygon` with vertices past the
CRS range (observed up to lon ≈ 180.6 on the FTW global runs). Tile
exporters treat those vertices as outside the world and drop the whole
cell, so dateline cells (Fiji, Chukotka, the Pacific zones) silently vanish
from any map built on the aggregate. The FTW catalogs carry a downstream
workaround that splits each affected cell into a wrapped MultiPolygon
before tiling
([`add_coverage.py`](https://github.com/fieldsoftheworld/ftw-global-data-catalog/blob/main/pipeline/add_coverage.py),
`wrap_antimeridian`) — a couple dozen cells globally per run.

## Where it comes from

- Cell boundaries come straight from the DuckDB community extension:
  `boundary_template="a5_cell_to_boundary({cell})"` and
  `poly_wkb_template` close the raw lon/lat vertex ring with
  `ST_MakePolygon(ST_MakeLine(...))` (`core/process/aggregate/by_a5.py:18-34`),
  applied in `wrap_grid_geometry` (`grid_common.py:328-363`). No
  antimeridian handling exists on this path (the only dateline-aware code,
  `_bbox_center_lon_sql` at `grid_common.py:176-191`, wraps the bucketing
  point, not the output polygon).
- `gpio process overview` regenerates parent-cell polygons through the same
  `wrap_grid_geometry` (`overview/rollup.py`), so overviews inherit the
  defect.

## Fix plan

1. Add a post-generation wrap step in `wrap_grid_geometry`
   (`grid_common.py:328-363`), applied whenever
   `ST_XMax(geom) > 180 OR ST_XMin(geom) < -180`:
   split the polygon into `ST_Union` of `ST_Intersection` with the world
   envelope and with copies translated ±360° (the same construction as the
   FTW `wrap_antimeridian`). Express it in SQL as a `CASE WHEN` around the
   existing `poly_wkb_template`, or — simpler and equally correct given the
   tiny affected count — flag affected rows in SQL and fix them on the
   returned arrow table in Python (shapely is not currently a dependency;
   a pure-SQL fix avoids adding one).
2. Because `overview` reuses `wrap_grid_geometry`, one fix covers both
   commands.
3. Emit the result as MultiPolygon where a split occurred; leave untouched
   cells as Polygon.

### Acceptance

- Aggregate a fixture of points spanning ±180 (e.g. lon 179.9 and −179.9 at
  a5 r7): every output vertex lies in [−180, 180], geometries are valid,
  and cell counts/metrics are unchanged versus before the fix.
- The same assertion holds for `process overview` parents of those cells.
- A regression test pins the wrapped MultiPolygon shape for one known
  dateline cell id.
