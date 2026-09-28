#!/bin/bash
# Full-detail per-year field boundary PMTiles (z0-13, standard tylertoo
# thinning) using tylertoo's native sharded workflow. Four stages, driven by
# MODE (all read the same GP2 stats-bearing input for row-group pruning):
#
#   YEAR=2025 MODE=plan   sbatch --export=ALL tile_fields.sbatch
#   YEAR=2025 MODE=coarse sbatch --export=ALL --mem=360G tile_fields.sbatch
#   for i in $(seq 0 7); do YEAR=2025 MODE=shard IDX=$i sbatch --export=ALL tile_fields.sbatch; done
#   YEAR=2025 MODE=merge  sbatch --export=ALL tile_fields.sbatch
#
# (shards can be submitted with --dependency=afterok:<coarse job>, merge with
# --dependency=afterok:<all shard jobs>)
#
# Measured on global 2025 (1.58B rows): coarse 4h12m + 8 shards 0.9-3.5h in
# parallel + merge 8.6 min = ~7h50m wall for the 115.5 GB z0-13 archive.
# The coarse job holds the full pass-1 feature table (~64 B/row -> ~97 GiB
# at 1.58B rows) plus winner grids: it needs --mem=360G (tylertoo#543).
#SBATCH --job-name=ftw-tile-fields
#SBATCH --partition=cpu
#SBATCH --account=bgtj-tgirails
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=192G
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}" "${MODE:?set MODE=plan|coarse|shard|merge}"
N="${N:-8}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
IN="${IN:-$PWD/global${YEAR}_gp2.parquet}"
TT="${TT:-$HOME/tylertoo-src/target/release/tylertoo}"
export TMPDIR="$PWD/tmp-${YEAR}-${MODE}${IDX:-}"   # /u spill — /tmp is tmpfs
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT
export RUST_LOG=info
export TYLERTOO_PROFILE_JSON="$PWD/profile-${YEAR}-${MODE}${IDX:-}.json"

# --row-group-size 100000: shard outputs run ~1B overview rows; the default
# 10k-row groups hit parquet's 32,767 row-group ceiling (tylertoo#507).
COMMON=(--min-zoom 0 --max-zoom 13 --layer-name fields --profile bounded
        --row-group-size 100000)

case "$MODE" in
  plan)
    "$TT" shard-plan "$IN" --shards "$N" --pivot 9 -o "shard-plan-${YEAR}.json"
    ;;
  coarse)
    "$TT" tiles "$IN" "coarse-${YEAR}.pmtiles" \
      --shard coarse --shard-plan "shard-plan-${YEAR}.json" \
      "${COMMON[@]}" --save-plan "convert-${YEAR}.plan"
    ;;
  shard)
    : "${IDX:?set IDX=0..N-1 for MODE=shard}"
    "$TT" tiles "$IN" "shard${IDX}-${YEAR}.pmtiles" \
      --shard "${IDX}/${N}" --shard-plan "shard-plan-${YEAR}.json" \
      "${COMMON[@]}" --plan "convert-${YEAR}.plan"
    ;;
  merge)
    # COARSE defaults to the fields coarse archive; override it with the a5
    # cells archive (COARSE=fields-<year>-a5r7.pmtiles) for the handover
    # product: cells z0-8, full fields z9-13, one archive (merge unions
    # disjoint tile ids and vector_layers, so mixed layers/zooms are fine).
    COARSE="${COARSE:-coarse-${YEAR}.pmtiles}"
    OUT="${OUT:-fields-${YEAR}.pmtiles}"
    SHARDS=()
    for i in $(seq 0 $((N - 1))); do SHARDS+=("shard${i}-${YEAR}.pmtiles"); done
    "$TT" merge "$OUT" "$COARSE" "${SHARDS[@]}" \
      --report "merge-report-${YEAR}.json" -f
    ls -la "$OUT"
    sha256sum "$OUT"
    ;;
esac
echo "FIELDS-${MODE}${IDX:-}-DONE"
