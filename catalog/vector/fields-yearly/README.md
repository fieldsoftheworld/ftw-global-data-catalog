# FTW Global (beta) — Fields by year (PMTiles)

Browsable field-boundary tiles for 2024, 2025. Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

Open it in the [data browser](https://source.coop/ftw/global-data-beta): each year is one PMTiles archive with a zoom handover — A5 r7 cell aggregates at z0–8 switching to the full field polygons (all parquet attributes) from z9.

## Layers

- `cells` (z0–8): per-cell `count`, `area_ha`, `avg_score`, `pct_covered` (A5 r7, ≈2,075.5 km² per cell).
- `fields` (z9–13): every predicted parcel with `id`, `metrics:area`, `metrics:perimeter`, `score` (constant columns are excluded).

## Styles

- **count** — Field count (A5 r7 → fields)
- **coverage** — Field coverage (A5 r7 → fields)
- **avg-size** — Mean field size (A5 r7 → fields)
- **field-prob** — Field probability (A5 r7 → fields)

The per-cell aggregates are also published as GeoParquet (`cells_a5r7_{year}.parquet`) for analysis; the source polygons live in the per-year [vector collections](../).
