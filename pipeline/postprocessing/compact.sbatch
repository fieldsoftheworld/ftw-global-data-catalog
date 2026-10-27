#!/bin/bash
#SBATCH --job-name=pp-compact
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=pp-compact-%A_%a.out
#
# compact_outlines.py encode over one shard of $OUT/outlines/$YEAR into $OUT/outlines-compact;
# the array index is the shard and NSHARDS the number of shards. Sizing from the production
# compaction of 2017-2025 (40 tasks a year): MaxRSS up to 7.3 GB, 4G ran out on some tiles
# under 300 MB, at most 15 min a task. DELETE_ORIGINAL=1 removes each raw tile once its compact
# file is verified; run it only after merge has succeeded.
#
#   sbatch --array=0-39 --export=ALL,YEAR=2025,NSHARDS=40,OUT=$OUT \
#     pipeline/postprocessing/compact.sbatch
set -euo pipefail
: "${YEAR:?}" "${NSHARDS:?}" "${OUT:?run root}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/postprocessing"
del=()
[[ ${DELETE_ORIGINAL:-0} == 1 ]] && del=(--delete-original)
"$PY" -u compact_outlines.py encode --year "$YEAR" --in-root "$OUT/outlines" \
  --out-root "$OUT/outlines-compact" --shard "${SLURM_ARRAY_TASK_ID:-0}" \
  --num-shards "$NSHARDS" "${del[@]}"
