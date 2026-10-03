#!/bin/bash
# Submit one year's PMTiles chain: stage -> {cells | plan -> coarse -> shards} -> merge.
# Usage: ./run_year.sh 2020   (sizes overridable via *_C / *_M env)
set -euo pipefail
cd "$(dirname "$0")"
y=${1:?year}
export YEAR=$y N=${N:-4} R=7
EX=(--account=bgtj-tgirails --partition=cpu,cpu_amd "--exclude=rails[04-15]" --export=ALL)
sub() { sbatch --parsable "${EX[@]}" "$@"; }
s=$(sub ${PREV_STAGE:+--dependency=afterok:$PREV_STAGE} -c"${STAGE_C:-16}" --mem="${STAGE_M:-64G}" -J "pm-stage-$y" stage.sbatch)
a=$(sub -c"${AGG_C:-16}" --mem="${AGG_M:-64G}" -J "pm-agg-$y" --dependency=afterok:$s aggregate_cells.sbatch)
c=$(sub -c"${CELL_C:-8}" --mem="${CELL_M:-32G}" -J "pm-cells-$y" --dependency=afterok:$a tile_cells.sbatch)
p=$(MODE=plan sub -c2 --mem=4G --time=00:30:00 -J "pm-plan-$y" --dependency=afterok:$s tile_fields.sbatch)
k=$(MODE=coarse sub -c"${COARSE_C:-16}" --mem="${COARSE_M:-40G}" -J "pm-coarse-$y" --dependency=afterok:$p tile_fields.sbatch)
sh=()
for i in $(seq 0 $((N - 1))); do
  sh+=("$(MODE=shard IDX=$i sub -c"${SHARD_C:-32}" --mem="${SHARD_M:-160G}" -J "pm-shard$i-$y" --dependency=afterok:$k tile_fields.sbatch)")
done
dep=$(IFS=:; echo "${sh[*]}")
m=$(MODE=merge COARSE="fields-$y-a5r7.pmtiles" sub -c8 --mem="${MERGE_M:-32G}" -J "pm-merge-$y" --dependency="afterok:$c:$dep" tile_fields.sbatch)
u=$(sub -J "pm-upload-$y" --dependency=afterok:$m upload_year.sbatch)
echo "$y: stage $s agg $a cells $c plan $p coarse $k shards ${sh[*]} merge $m upload $u"
