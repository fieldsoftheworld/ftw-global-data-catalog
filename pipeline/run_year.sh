#!/bin/bash
# Submit one year's PMTiles chain: stage -> {cells | plan -> coarse -> shards} -> merge -> check -> upload.
# Usage: ./run_year.sh 2020   (sizes overridable via *_C / *_M env)
#   N=8        shards of the fields archive (default 4). With N >= 8 a shard defaults to 16 CPUs and
#              70G instead of 32 CPUs and 160G: the z9-13 tile space is split finer, each shard
#              peaks at 31-48 GB (N=8) instead of 105-168 GB (N=4), and places on a shared node far
#              sooner. The tiles are the same either way (shards partition the z9 tile space and the
#              merge unions them).
#   EXCLUDE=   nodes to avoid (default rails[04-15], the GPU nodes); EXCLUDE= with nothing lifts it.
#   SRC_ROOT=  the published zone files ({SRC_ROOT}/{year}/zone=*/utm*.parquet): runs pmcheck.py on
#              the merged archive (needs duckdb, mapbox_vector_tile and pmtiles in PY, default
#              python3) and uploads only if it passes. Without it nothing is checked.
#   REF_REPORT=a merge report of an earlier build of the year, for pmcheck's per-zoom comparison.
#   UPLOAD=0   hold the upload (what the 2026-10 run did until each year's QA was reviewed).
set -euo pipefail
cd "$(dirname "$0")"
y=${1:?year}
export YEAR=$y N=${N:-4} R=7
if [[ $N -ge 8 ]]; then shard_c=16 shard_m=70G; else shard_c=32 shard_m=160G; fi
EX=(--account=bgtj-tgirails --partition=cpu,cpu_amd --export=ALL)
[[ -n "${EXCLUDE-rails[04-15]}" ]] && EX+=("--exclude=${EXCLUDE-rails[04-15]}")
sub() { sbatch --parsable "${EX[@]}" "$@"; }
s=$(sub ${PREV_STAGE:+--dependency=afterok:$PREV_STAGE} -c"${STAGE_C:-16}" --mem="${STAGE_M:-64G}" -J "pm-stage-$y" stage.sbatch)
a=$(sub -c"${AGG_C:-16}" --mem="${AGG_M:-64G}" -J "pm-agg-$y" --dependency=afterok:$s aggregate_cells.sbatch)
c=$(sub -c"${CELL_C:-8}" --mem="${CELL_M:-32G}" -J "pm-cells-$y" --dependency=afterok:$a tile_cells.sbatch)
p=$(MODE=plan sub -c2 --mem=4G --time=00:30:00 -J "pm-plan-$y" --dependency=afterok:$s tile_fields.sbatch)
k=$(MODE=coarse sub -c"${COARSE_C:-16}" --mem="${COARSE_M:-40G}" -J "pm-coarse-$y" --dependency=afterok:$p tile_fields.sbatch)
sh=()
for i in $(seq 0 $((N - 1))); do
  sh+=("$(MODE=shard IDX=$i sub -c"${SHARD_C:-$shard_c}" --mem="${SHARD_M:-$shard_m}" -J "pm-shard$i-$y" --dependency=afterok:$k tile_fields.sbatch)")
done
dep=$(IFS=:; echo "${sh[*]}")
m=$(MODE=merge COARSE="fields-$y-a5r7.pmtiles" sub -c8 --mem="${MERGE_M:-32G}" -J "pm-merge-$y" --dependency="afterok:$c:$dep" tile_fields.sbatch)
ck=none
if [[ -n ${SRC_ROOT:-} ]]; then
  ck=$(sub -c2 --mem=8G --time=01:00:00 -J "pm-check-$y" --dependency=afterok:$m --output="pm-check-$y-%j.out" \
    --wrap "${PY:-python3} $PWD/pmcheck.py $y fields-$y.pmtiles $SRC_ROOT merge-report-$y.json${REF_REPORT:+ $REF_REPORT}")
fi
u=held
if [[ ${UPLOAD:-1} == 1 ]]; then
  u=$(sub -J "pm-upload-$y" --dependency=afterok:$([[ $ck == none ]] && echo "$m" || echo "$ck") upload_year.sbatch)
fi
echo "$y: stage $s agg $a cells $c plan $p coarse $k shards ${sh[*]} merge $m check $ck upload $u"
