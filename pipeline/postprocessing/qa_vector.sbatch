#!/bin/bash
#SBATCH --job-name=pp-qa
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=20G
#SBATCH --time=02:00:00
#SBATCH --output=pp-qa-%j.out
#
# qa_vector.py over one converted year into $OUT/qa/vector_$YEAR.json. DuckDB is capped at 16 GB.
#
#   sbatch --export=ALL,YEAR=2025,OUT=$OUT pipeline/postprocessing/qa_vector.sbatch
# Optional: FTW_LAND_POLYGONS (sea sample), FOOTPRINTS (north-edge check), RELEASE (a released
# tree to compare with).
set -euo pipefail
: "${YEAR:?}" "${OUT:?run root}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/postprocessing"
"$PY" -u -W ignore qa_vector.py "$YEAR" --root "$OUT/fiboa" --out "$OUT/qa/vector_$YEAR.json" \
  --memory-limit 16GB ${FOOTPRINTS:+--footprints "$FOOTPRINTS"} ${RELEASE:+--release "$RELEASE"}
