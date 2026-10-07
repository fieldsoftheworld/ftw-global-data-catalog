#!/bin/bash
# Multi-resolution A5 cell tiles for the viewer: cells-{year}.pmtiles, one A5 resolution per zoom
# (tile z = R - ZOFF). The defaults are the published ladder, r4 at z0 ... r11 at z7, which keeps a
# hexagon 5-9 px wide at every zoom (r4 = 1,000 cells of 133,000 km2, r11 = 2.8 km cells, 4.1 M of
# them in 2025, 140 MB a year).
#   cp pipeline/* ~/ftw-2e-pipeline/ && cd ~/ftw-2e-pipeline        # next to global{YEAR}_gp2.parquet
#   YEAR=2025 sbatch --export=ALL web_cells.sbatch
#   YEARS="2017 2018 2019" sbatch --array=0-2%2 --export=ALL web_cells.sbatch
#   IN=<parquet>   the merged parcel parquet (default global${YEAR}_gp2.parquet, the input of
#                  aggregate_cells.sbatch); cells_a5r7_${YEAR}.parquet next to it is compared with the
#                  r7 roll-up (counts exactly) when present
#   OUT=<dir>      output directory (default ./cells)
#   ZOFF/COARSEST/FINEST  the ladder: resolutions COARSEST..FINEST, resolution R at tile z = R - ZOFF
#   SKIP_AGG=1     skip the aggregation and tile the cells_a5r*_${YEAR}_plain.parquet already in $OUT
# Sized from sacct on 2025 (147 M parcels): the scan of the 118 GB parquet plus one a5_lonlat_to_cell
# pass per resolution (r11 to r7) is about 7 min on 4 CPUs; sacct MaxRSS reads 24 GB because it sits
# at the cgroup limit (a 14 GB DuckDB limit inside it), tiling and merging take about a minute.
# Not on the GPU nodes.
#SBATCH --job-name=web-cells
#SBATCH --partition=cpu,cpu_amd
#SBATCH --account=bgtj-tgirails
#SBATCH --exclude=rails[04-06]
#SBATCH --time=00:45:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
# one year (YEAR=2025) or an array over YEARS="2017 2018 ..." (sbatch --array=0-8%2)
if [ -z "${YEAR:-}" ] && [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then
  read -ra year_list <<<"${YEARS:?set YEARS for an array job}"
  YEAR=${year_list[$SLURM_ARRAY_TASK_ID]}
fi
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
IN="${IN:-$PWD/global${YEAR}_gp2.parquet}"
OUT="${OUT:-$PWD/cells}"
COARSEST="${COARSEST:-4}"
FINEST="${FINEST:-11}"
ZOFF="${ZOFF:-4}"
[ "$COARSEST" -ge "$ZOFF" ] || { echo "COARSEST ($COARSEST) must be >= ZOFF ($ZOFF)" >&2; exit 2; }
VENV="${VENV:-$HOME/ftw-us-tiles/venv}"
TT="${TT:-$HOME/tylertoo-src/target/release/tylertoo}"
PMT="${PMT:-$HOME/.local/bin/pmtiles}"
export QUERY_FARM_TELEMETRY_OPT_OUT=1 TMPDIR="$OUT/tmp-$YEAR"   # /u spill: /tmp is tmpfs
mkdir -p "$OUT" "$TMPDIR"
trap 'rm -rf "$TMPDIR" "$OUT/duck-tmp-$YEAR"' EXIT

if [ -z "${SKIP_AGG:-}" ]; then
  CHECK=(); [ -f "$(dirname "$IN")/cells_a5r7_${YEAR}.parquet" ] && CHECK=(--check-r7 "$(dirname "$IN")/cells_a5r7_${YEAR}.parquet")
  "$VENV/bin/python" web_cells.py "$YEAR" --in "$IN" --out "$OUT" \
    --finest "$FINEST" --coarsest "$COARSEST" --memory 14GB "${CHECK[@]}"
fi

# One zoom level per resolution: tile z = R - ZOFF. The finest resolution (r11, z7) is overzoomed by
# the viewer above its zoom, where the field polygons cross-fade in.
parts=()
for R in $(seq "$COARSEST" "$FINEST"); do
  Z=$((R - ZOFF))
  if [ ! -f "$OUT/cells_a5r${R}_${YEAR}.parquet" ]; then
    "$VENV/bin/gpio" convert geoparquet "$OUT/cells_a5r${R}_${YEAR}_plain.parquet" "$OUT/cells_a5r${R}_${YEAR}.parquet" --geoparquet-version 2.0
  fi
  rm -f "$OUT/part-r${R}-${YEAR}.pmtiles"
  "$TT" tiles "$OUT/cells_a5r${R}_${YEAR}.parquet" "$OUT/part-r${R}-${YEAR}.pmtiles" \
    --min-zoom "$Z" --max-zoom "$Z" --layer-name cells --verbatim --exclude-property a5_cell --profile bounded
  parts+=("$OUT/part-r${R}-${YEAR}.pmtiles")
done
rm -f "$OUT/cells-${YEAR}.pmtiles"
"$PMT" merge "${parts[@]}" "$OUT/cells-${YEAR}.pmtiles"
rm -f "${parts[@]}" "$OUT/cells_a5r"*"_${YEAR}_plain.parquet"
ls -la "$OUT/cells-${YEAR}.pmtiles"
sha256sum "$OUT/cells-${YEAR}.pmtiles"
"$PMT" show "$OUT/cells-${YEAR}.pmtiles"
echo "WEB-CELLS-DONE"
