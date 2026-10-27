#!/bin/bash
#SBATCH --job-name=pp-simplify
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=pp-simplify-%A_%a.out
#
# simplify_polygons.py, one worker, over one shard of the outline files in [MIN_MB, MAX_MB);
# the array index is the shard and NSHARDS the number of shards. Peak memory is about 25 times
# the outline file size (2025: 38 MB -> 0.9 GB in 11 s, 931 MB -> 23 GB in 3 min), so
# run_year.sh submits one array per size class, each with its own --mem.
#
#   sbatch --array=0-7 --mem=40G --export=ALL,YEAR=2025,NSHARDS=8,MIN_MB=200,OUT=$OUT \
#     pipeline/postprocessing/simplify.sbatch
set -euo pipefail
: "${YEAR:?}" "${NSHARDS:?}" "${OUT:?run root}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/postprocessing"
"$PY" -u -W ignore simplify_polygons.py --year "$YEAR" --in-root "$OUT/outlines" \
  --out-root "$OUT/simplified" --workers 1 --min-mb "${MIN_MB:-0}" --max-mb "${MAX_MB:-inf}" \
  --shard "${SLURM_ARRAY_TASK_ID:-0}" --num-shards "$NSHARDS"
