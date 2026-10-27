#!/bin/bash
#SBATCH --job-name=pp-merge
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=6
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=pp-merge-%j.out
#
# merge_polygons.py over one simplified year, then cross_zone_seams.py. DuckDB holds 48 GB and
# spills to $OUT/scratch/duckdb. Sizing from the production merge of 2017-2025 (DuckDB at 48 GB):
# 10-24 min, 4-5 cores busy on average, memory at the 64G limit.
#
#   sbatch --export=ALL,YEAR=2025,OUT=$OUT,TILES=$PWD/tiles.txt pipeline/postprocessing/merge.sbatch
# EMPTY names tiles without a score COG (merge --empty-list); unset, there are none.
set -euo pipefail
: "${YEAR:?}" "${OUT:?run root}" "${TILES:?tile list}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/postprocessing"
"$PY" -u -W ignore merge_polygons.py --year "$YEAR" --in-root "$OUT/simplified" \
  --out-root "$OUT/merged" --no-aux --tmp-dir "$OUT/scratch/duckdb" \
  --keep-list "$TILES" --empty-list "${EMPTY:-/dev/null}" --memory 48GB
