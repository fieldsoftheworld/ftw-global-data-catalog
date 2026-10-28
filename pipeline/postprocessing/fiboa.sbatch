#!/bin/bash
#SBATCH --job-name=pp-fiboa
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:30:00
#SBATCH --output=pp-fiboa-%j.out
#
# Submits one year's fiboa conversion with fiboa_ranges.py submit (one job for a small zone, a
# plan -> range array -> stitch chain for a large one), then qa_vector.sbatch after all of them.
# It runs after merge because the jobs and their sizes follow from each zone's merged row count.
#
#   sbatch --dependency=afterok:<merge job> --export=ALL,YEAR=2025,OUT=$OUT \
#     pipeline/postprocessing/fiboa.sbatch
# Reads FTW_LAND_POLYGONS (the sea rule), SBATCH_PARTITION and EXCLUDE (nodes to keep free).
set -euo pipefail
: "${YEAR:?}" "${OUT:?run root}" "${FTW_LAND_POLYGONS:?OSM land polygons GeoParquet}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
export REPO PY
cd "$REPO/pipeline/postprocessing"
# submit prints one line per zone ending in the id of the zone's last job
ids=$("$PY" -u fiboa_ranges.py submit --year "$YEAR" --in-root "$OUT/merged" \
  --out-root "$OUT/fiboa" --tmp-dir "$OUT/scratch/duckdb" \
  --work-root "$OUT/scratch/fiboa-ranges" --log-dir "$OUT/logs" \
  ${SBATCH_PARTITION:+--partition "$SBATCH_PARTITION"} ${EXCLUDE:+--exclude "$EXCLUDE"} \
  | tee /dev/stderr | awk '{print $NF}' | paste -sd:)
if [[ -z $ids ]]; then
  echo "fiboa_ranges.py submitted no jobs" >&2
  exit 1
fi
sbatch --parsable --export=ALL --kill-on-invalid-dep=yes --dependency="afterok:$ids" \
  ${EXCLUDE:+--exclude="$EXCLUDE"} -J "pp-qa-$YEAR" --output="$OUT/logs/%x_%j.out" \
  qa_vector.sbatch
