# How the published PMTiles were built

The nine `vector/{year}/fields-{year}.pmtiles` archives and their `cells_a5r7_{year}.parquet`
files were built with the scripts in this directory (`run_year.sh`, `stage.sbatch`,
`aggregate_cells.sbatch`, `tile_cells.sbatch`, `tile_fields.sbatch`, `upload_year.sbatch`)
from the 54 zone files of each year's published vectors, one year at a time. The only script
change was to pin `PATH` to one venv. Tools: gpio 1.6.0 and DuckDB 1.5.6 in the build venv,
tylertoo at `dabed9f`; `pmcheck.py` ran in a second venv with DuckDB 1.5.6, `pmtiles` 3.8.1 and
`mapbox_vector_tile` 2.2.0. Each archive holds
the `cells` layer (A5 resolution 7, z0-8, every cell verbatim) and the `fields` layer (the
parcels, z9-13, tylertoo's `bounded` profile with `collection`, `determination:datetime`
and `determination:method` left out of the tiles).

```sh
cp pipeline/* ~/ftw-2e-pipeline/ && cd ~/ftw-2e-pipeline
SRC_ROOT=<dir holding {year}/zone=*/utm*.parquet> UPLOAD=0 ./run_year.sh 2025            # 4 shards
N=8 EXCLUDE= SRC_ROOT=... UPLOAD=0 ./run_year.sh 2019                                    # 8 shards
```

## Shards

2017, 2018 and 2021-2025 were built with 4 shards of 32 CPUs and 160 GB. A 4-shard job peaks
at 96-168 GB (`MaxRSS`, 2025), which waited hours for a free node when the cluster was busy,
so 2019 and 2020 were resumed from the plan step with 8 shards of 16 CPUs and 70 GB. They
peaked at 31-48 GB and each took 12-36 minutes. The tiles are the same either way: the
shard plan cuts the z9 tile space into ranges of about equal row counts, every shard tiles
its ranges, and the merge unions the disjoint tile sets with the cells archive. 8 shards is
now the default for a busy cluster (`N=8` in `run_year.sh` sets the smaller sizes).

| step | 2025 (4 shards) | notes |
|---|---|---|
| stage (54 zones to one GeoParquet 2.0) | 47 min, 16 CPUs, 64 GB | 30-62 min across years, shared filesystem |
| A5 r7 aggregate | 1 min | 35,935 cells, 146.7 M parcels |
| cells archive, z0-8 | seconds | |
| shard plan, plan-only coarse | 7 min, 40 GB | 5-9 min across years |
| shards, z9-13 | 19-45 min | 12-36 min with 8 shards |
| merge with the cells archive | 5 min | 3-6 min across years; 5 archives (cells + 4 shards) |

## Check and upload

`pmcheck.py` (`SRC_ROOT=` in `run_year.sh`) opens the merged archive and fails unless the header
spans z0-13, the layers are exactly `cells` (z0-8) and `fields` (z9-13), a sample tile of each
layer exists at Iowa (z4, z8, z9, z13), the z13 tile over each of eight places (Iowa, Kagera,
the Pampas, Saskatchewan, Punjab, Jaeren, the Netherlands at 6.02°E and Thailand at 102.2°E)
has features whenever the zone files have parcels in it, and, with `REF_REPORT=`, every
zoom's tile count is within 15% of an earlier build's merge report. All nine archives passed.
Uploads go through `s3_put_retry.py` (16 MiB parts, each retried on the proxy's 520s) and
are checked for size and multipart ETag; `upload_year.sbatch` records size and multihash in
`staging-data/checksums/tiles_meta.json`.

| year | tiles | size | shards |
|---|---:|---:|---:|
| 2017 | 3,951,719 | 28.50 GB | 4 |
| 2018 | 3,969,160 | 28.24 GB | 4 |
| 2019 | 3,949,657 | 28.10 GB | 8 |
| 2020 | 3,957,345 | 29.60 GB | 8 |
| 2021 | 3,974,280 | 30.52 GB | 4 |
| 2022 | 4,007,586 | 30.70 GB | 4 |
| 2023 | 4,035,841 | 30.49 GB | 4 |
| 2024 | 3,983,274 | 28.78 GB | 4 |
| 2025 | 4,004,322 | 30.80 GB | 4 |

## Hexagons for the viewer: `cells-{year}.pmtiles`

The r7 `cells` layer inside `fields-{year}.pmtiles` is one hexagon size for z0-8, which is
~2 px wide on a globe and 75 px at z8. `web_cells.py` and `web_cells.sbatch` build a second
archive per year, `vector/{year}/cells-{year}.pmtiles`, with one A5 resolution per zoom, r4
at z0 to r11 at z7 (tile z = R - 4), so a hexagon is 5-9 px wide at every zoom: r4 is 1,000
cells of 133,000 km², r11 is 2.8 km cells, 4.1 M of them in 2025, 140 MB a year. r7 to r11
are bucketed straight from the parcel centroids with `a5_lonlat_to_cell`, as `gpio process
aggregate a5` does, so r7 reproduces the published `cells_a5r7_{year}.parquet` (checked with
`--check-r7`); r4 to r6 are bucketed from the centres of the r7 cells, because A5's hierarchy
is not geometrically nested (the r7 parent of a point's r10 cell is that point's r7 cell only
65% of the time). Each cell carries `count`, `density` (fields per 1,000 km²), `area_ha`,
`avg_score` and `pct_covered`. The per-resolution archives are merged with the `pmtiles` CLI
(go-pmtiles 1.30.3). A year takes about 7 minutes on 4 CPUs and 24 GB:

```sh
YEARS="2017 2018 2019 2020 2021 2022 2023 2024 2025" \
  sbatch --array=0-8%2 --export=ALL web_cells.sbatch        # cells/cells-{year}.pmtiles
python pipeline/upload_cells.py 2025 cells/cells-2025.pmtiles   # refuses to overwrite; --replace-existing
```

These were built and uploaded on 2026-10-07, after the vectors and `fields-` archives, and
replaced an earlier r3-r10 ladder under the same object names for the years that had one
(2017 and 2021-2025). The earlier files are not in the bucket.
