#!/bin/bash
#SBATCH --job-name=pp-outlines
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=26G
#SBATCH --time=08:00:00
#SBATCH --output=pp-outlines-%A_%a.out
#
# outlines.py over one shard of a tile list; the array index is the shard and NSHARDS the number
# of shards, so a single task can be rerun alone (--array=14). Tiles whose outline is current
# are skipped. Sizing (fbp.run_windowed, one worker): 2 CPUs, 17.5 GB MaxRSS, ~24 min a tile.
#
#   sbatch --array=0-99%50 --export=ALL,YEAR=2025,NSHARDS=100,OUT=$OUT,SCORES=$SCORES,\
#     INDEX_DIR=$INDEX,TILES=$PWD/tiles.txt pipeline/postprocessing/outlines.sbatch
# The QA context reads CDSE EODATA: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY first.
set -euo pipefail
: "${YEAR:?}" "${NSHARDS:?}" "${OUT:?run root}" "${SCORES:?score root}" \
  "${INDEX_DIR:?holds tile_index_{year}*.parquet}" "${TILES:?tile list}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/postprocessing"
"$PY" -u -W ignore outlines.py --year "$YEAR" --scores "$SCORES" --out-root "$OUT/outlines" \
  --index-dir "$INDEX_DIR" --tile-list "$TILES" --workers 1 \
  --shard "${SLURM_ARRAY_TASK_ID:-0}" --num-shards "$NSHARDS"
