#!/bin/bash
# Per-item browse thumbnails: one PNG per raster COG, as a Slurm array.
#
#   cp pipeline/* ~/ftw-beta-pipeline/ && cd ~/ftw-beta-pipeline
#
#   # 1. Rehearsal: two real tiles, no array (about 10 seconds).
#   YEAR=2025 TILES=31UFU_0_0,32ULD_0_0 sbatch --export=ALL thumbnails.sbatch
#
#   # 2. One year. 7,467 tiles at CHUNK=100 is tasks 0..74.
#   YEAR=2025 sbatch --export=ALL --array=0-74%16 thumbnails.sbatch
#
#   # 3. The other eight years, one array each (7,466 tiles -> 0..74 too).
#   for y in 2024 2023 2022 2021 2020 2019 2018 2017; do
#     YEAR=$y sbatch --export=ALL --array=0-74%16 thumbnails.sbatch
#   done
#
#   # 4. One failed task, resubmitted alone. CHUNK must match the
#   #    original run or the slice is a different set of tiles.
#   YEAR=2025 sbatch --export=ALL --array=37 thumbnails.sbatch
#
# The %16 throttle is about the network, not the cores: 16 tasks x
# WORKERS=8 is 128 range reads in flight against one host. The s2
# catalog sustained ~145 requests/second against this endpoint from a
# compute node with zero denials, so that is proven territory; raise it
# only with that measurement in hand.
#
# Resumable by design: a task skips any thumbnail already on disk, so a
# task that walls or dies is resubmitted unchanged and a whole-year
# resubmit costs only the renders that are missing. A task that had
# individual tile failures exits non-zero and names them, and
# resubmitting that one task retries exactly those.
#
# Chain behind a year's overview with --dependency=afterok:<overview job>
# if you want one submission per year; the two products are independent
# otherwise and the thumbnails never read the overview.
#SBATCH --job-name=ftw-browse-thumbs
#SBATCH --partition=cpu,cpu_amd
#SBATCH --account=bgtj-tgirails
#SBATCH --time=2:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
export PATH="/u/cholmes/micromamba/envs/ftw/bin:$HOME/ftw-us-tiles/venv/bin:$PATH"
export PROJ_DATA="${PROJ_DATA:-/u/cholmes/micromamba/envs/ftw/share/proj}"
export TZ=UTC

OUT="${OUT:-$PWD/browse-publish}"
# One temp dir per array task: the small per-tile GeoTIFF lands here and
# /tmp on a compute node is tmpfs that counts against the job cgroup.
export TMPDIR="$PWD/tmp-thumbs-${YEAR}-${SLURM_ARRAY_TASK_ID:-single}"
mkdir -p "$TMPDIR" "$OUT"
trap 'rm -rf "$TMPDIR"' EXIT

# CHUNK is the array's tile-to-task mapping and must be identical between
# the original run and any resubmit of one task, so it lives here and
# nowhere else.
CHUNK="${CHUNK:-100}"
WORKERS="${WORKERS:-8}"
WIDTH="${WIDTH:-512}"
MIN_PROB="${MIN_PROB:-0.10}"
# One shared local copy of the 2.6 MB index, fetched by whichever task
# gets there first (the write is atomic, so a race is harmless). Without
# it each of the 675 tasks in the campaign reads the remote parquet.
CACHE="${CACHE:-$PWD/raster-index.parquet}"

ARGS=(--year "$YEAR" --out "$OUT" --index-cache "$CACHE"
      --width "$WIDTH" --min-prob "$MIN_PROB"
      --workers "$WORKERS" --chunk "$CHUNK")
[ -n "${INDEX:-}" ] && ARGS+=(--index "$INDEX")
[ -n "${TILES:-}" ] && ARGS+=(--tiles "$TILES")
[ -n "${BBOX:-}" ]  && ARGS+=(--bbox "$BBOX")
# --task slices the year; without an array (the rehearsal) do the lot.
[ -n "${SLURM_ARRAY_TASK_ID:-}" ] && [ -z "${TILES:-}" ] \
    && ARGS+=(--task "$SLURM_ARRAY_TASK_ID")

echo "== thumbs $YEAR task=${SLURM_ARRAY_TASK_ID:-all} chunk=$CHUNK workers=$WORKERS"
python3 make_item_thumbnails.py "${ARGS[@]}"

echo "THUMBS-${YEAR}-${SLURM_ARRAY_TASK_ID:-all}-DONE"
