#!/bin/bash
#SBATCH --job-name=validate-cogs
#SBATCH --account=bgtj-tgirails
#SBATCH --partition=cpu,cpu_amd
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:45:00
#SBATCH --output=validate-cogs-%A_%a.out
#
# gate.py validate over one shard of a year's COGs; the array index is the shard.
# Sizing from the production run (job 257872): about 4 s a tile plus 10 s per fully decoded
# tile per worker, under 1 GB per worker, 5.9 GB MaxRSS with page cache for 4 workers.
#
#   sbatch --array=0-19 --export=ALL,YEAR=2025,ROOT=$OUT,FP=$FP pipeline/inference/validate.sbatch
set -euo pipefail
: "${YEAR:?}" "${ROOT:?stream.py --out-root}" "${FP:?run fingerprint}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/inference"
"$PY" -u -W ignore gate.py validate --year "$YEAR" --root "$ROOT" --fingerprint "$FP" \
  --out "$ROOT/qa/validate_$YEAR/shard_${SLURM_ARRAY_TASK_ID:-0}.csv"
