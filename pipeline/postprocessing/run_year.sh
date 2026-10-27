#!/bin/bash
# Submit one year's polygon postprocessing on Slurm, each stage afterok on the one before:
#   outlines (array) -> simplify (one array per size class) -> merge -> fiboa.sbatch, which
#   submits the per-zone conversion jobs (fiboa_ranges.py submit) and qa_vector.sbatch after them.
# Every stage resumes on its fingerprints, so after a failure the same command redoes only the
# work that is not current, and FROM= starts the chain at a later stage.
#
#   export SBATCH_ACCOUNT=<account> SBATCH_PARTITION=cpu EXCLUDE='rails[04-15]'
#   OUT=/scratch/ftw/2025 SCORES=/scratch/ftw/raster INDEX_DIR=index TILES=tiles_2025.txt \
#     FTW_LAND_POLYGONS=land_polygons.parquet pipeline/postprocessing/run_year.sh 2025
#
# Required: OUT (run root; the stages write outlines/, simplified/, merged/, fiboa/, qa/,
#   scratch/ and logs/ under it), TILES (the year's expected tiles, one per line; merge
#   --keep-list), FTW_LAND_POLYGONS (the sea rule's GeoParquet), and for the outline stage
#   SCORES (outlines.py --scores) and INDEX_DIR (holds tile_index_{year}*.parquet).
# Optional: EMPTY (tiles without a score COG; outlines skips them, merge --empty-list),
#   EXCLUDE (nodes to keep free), TILES_PER_TASK (outline tiles per array task, default 12),
#   THROTTLE (outline tasks at once, default 50), FROM (outlines|simplify|merge|fiboa|qa),
#   AFTER (a job id the first stage waits for), PY (default $REPO/.venv/bin/python).
# sbatch reads the account and partition from SBATCH_ACCOUNT and SBATCH_PARTITION.
set -euo pipefail
y=${1:?year}
: "${OUT:?run root}" "${TILES:?tile list}" "${FTW_LAND_POLYGONS:?OSM land polygons GeoParquet}"
stages=(outlines simplify merge fiboa qa)
start=-1
for i in "${!stages[@]}"; do [[ ${stages[i]} == "${FROM:-outlines}" ]] && start=$i; done
if ((start < 0)); then
  echo "FROM must be one of: ${stages[*]}" >&2
  exit 2
fi
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT=$(realpath -m "$OUT")
TILES=$(realpath "$TILES")
EMPTY=$(realpath "${EMPTY:-/dev/null}")
FTW_LAND_POLYGONS=$(realpath "$FTW_LAND_POLYGONS")
PY=${PY:-$REPO/.venv/bin/python}
EXCLUDE=${EXCLUDE:-}
export YEAR=$y REPO OUT TILES EMPTY FTW_LAND_POLYGONS PY EXCLUDE
mkdir -p "$OUT/logs"
cd "$REPO/pipeline/postprocessing"

EX=(--parsable --export=ALL --kill-on-invalid-dep=yes)
if [[ -n $EXCLUDE ]]; then EX+=("--exclude=$EXCLUDE"); fi
dep=${AFTER:-}
sub() { sbatch "${EX[@]}" ${dep:+"--dependency=afterok:$dep"} "$@"; }
log=""

if ((start <= 0)); then
  : "${SCORES:?score root}" "${INDEX_DIR:?holds tile_index_{year}*.parquet}"
  SCORES=$(realpath "$SCORES")
  INDEX_DIR=$(realpath "$INDEX_DIR")
  export SCORES INDEX_DIR
  # the expected tiles minus the ones without a score COG, which have nothing to outline
  list=$OUT/lists/outlines_$y.txt
  mkdir -p "$OUT/lists"
  awk 'FILENAME == ARGV[1] {skip[$1]; next} NF && !($1 in skip) {print $1}' "$EMPTY" "$TILES" \
    | LC_ALL=C sort -u >"$list"
  n=$(wc -l <"$list")
  if ((n == 0)); then
    echo "no tiles to outline in $TILES" >&2
    exit 1
  fi
  per=${TILES_PER_TASK:-12}
  tasks=$(((n + per - 1) / per))
  dep=$(NSHARDS=$tasks TILES=$list sub -J "pp-outlines-$y" \
    --array="0-$((tasks - 1))%${THROTTLE:-50}" --output="$OUT/logs/%x_%A_%a.out" outlines.sbatch)
  log+=" | outlines $dep ($n tiles, $tasks tasks)"
fi

if ((start <= 1)); then
  # class:min MB:max MB:--mem:tasks; peak memory is about 25 times the outline file size
  ids=()
  for spec in s:0:40:2G:32 m:40:200:8G:16 l:200:inf:40G:8; do
    IFS=: read -r c lo hi mem k <<<"$spec"
    ids+=("$(NSHARDS=$k MIN_MB=$lo MAX_MB=$hi sub -J "pp-simplify-$c-$y" --array="0-$((k - 1))" \
      --mem="$mem" --output="$OUT/logs/%x_%A_%a.out" simplify.sbatch)")
  done
  dep=$(IFS=:; echo "${ids[*]}")
  log+=" | simplify $dep"
fi

if ((start <= 2)); then
  dep=$(sub -J "pp-merge-$y" --output="$OUT/logs/%x_%j.out" merge.sbatch)
  log+=" | merge $dep"
fi

if ((start <= 3)); then
  dep=$(sub -J "pp-fiboa-$y" --output="$OUT/logs/%x_%j.out" fiboa.sbatch)
  log+=" | fiboa $dep (submits the zone jobs, then qa)"
else
  dep=$(sub -J "pp-qa-$y" --output="$OUT/logs/%x_%j.out" qa_vector.sbatch)
  log+=" | qa $dep"
fi
echo "$y$log"
