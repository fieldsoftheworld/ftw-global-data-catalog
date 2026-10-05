#!/bin/bash
# One year's global browse overview COG + its thumbnail.
#
#   cp pipeline/* ~/ftw-beta-pipeline/ && cd ~/ftw-beta-pipeline
#   YEAR=2025 sbatch --export=ALL overview.sbatch
#
# Smoke rehearsal first (two UTM zones of the Netherlands, ~2 minutes;
# BBOX also makes the job skip the full-year tile list):
#
#   YEAR=2025 BBOX=4,51,7,53 JOBS=4 sbatch --export=ALL overview.sbatch
#
# The nine years are independent, so they go as nine submissions rather
# than an array -- each one is hours of network-bound work and the
# throttle that matters is how many range reads hit data.source.coop at
# once, not how many cores are free:
#
#   for y in 2025 2024 2023 2022 2021 2020 2019 2018 2017; do
#     YEAR=$y sbatch --export=ALL overview.sbatch
#   done
#
# Chain the thumbnail array behind a year's overview with
# --dependency=afterok:<this job id> if you want one submission per year
# to cover both products; they are independent otherwise and the
# thumbnails do not read the overview.
#
# Resumable, which is the point of KEEP_SCRATCH: the per-zone VRTs and
# warped GeoTIFFs stay in WORK/<year>/overview-scratch, so a job that
# hits the wall clock is resubmitted unchanged and redoes only the zones
# that had not finished. Do not clean that directory mid-build.
#SBATCH --job-name=ftw-browse-overview
#SBATCH --partition=cpu,cpu_amd
#SBATCH --account=bgtj-tgirails
#SBATCH --time=8:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
export PATH="/u/cholmes/micromamba/envs/ftw/bin:$HOME/ftw-us-tiles/venv/bin:$PATH"
export PROJ_DATA="${PROJ_DATA:-/u/cholmes/micromamba/envs/ftw/share/proj}"
export TZ=UTC

WORK="${WORK:-$PWD/browse-work}"
OUT="${OUT:-$PWD/browse-publish}"
# Per-year temp isolation: concurrent year jobs share this submit dir.
export TMPDIR="$PWD/tmp-overview-${YEAR}"   # /u spill — /tmp is tmpfs
mkdir -p "$TMPDIR" "$WORK" "$OUT"

# JOBS is warp concurrency and it is what sets peak memory: the s2
# catalog measured ~56.5 GB at 32 concurrent warps of a 3-band mosaic of
# the same shape, flat once it reached steady state. 16 here leaves real
# headroom under --mem=64G. If a year ever peaks higher, LOWER JOBS
# rather than raising --mem: 120 G does not schedule on this cluster
# under normal load (it sits on (Priority) indefinitely) while 64 G
# starts in seconds. Staging concurrency is min(64, JOBS*4), so anything
# from JOBS=16 up still stages at the full 64 and costs nothing in the
# phase that is pure network latency.
JOBS="${JOBS:-16}"
ZOOM="${ZOOM:-10}"          # fixed across all nine years — see make_overview.py
OVERSAMPLE="${OVERSAMPLE:-1.1}"
MIN_PROB="${MIN_PROB:-0.10}"

ARGS=(--year "$YEAR" --work "$WORK" --out "$OUT"
      --zoom "$ZOOM" --oversample "$OVERSAMPLE" --min-prob "$MIN_PROB"
      --jobs "$JOBS" --keep-scratch --thumbnail)
[ -n "${INDEX:-}" ] && ARGS+=(--index "$INDEX")
[ -n "${BBOX:-}" ]  && ARGS+=(--bbox "$BBOX")
[ -n "${TILES:-}" ] && ARGS+=(--tiles "$TILES")

echo "== overview $YEAR  zoom=$ZOOM jobs=$JOBS out=$OUT"
python3 make_overview.py "${ARGS[@]}"

ls -la "$OUT/raster/$YEAR/"
echo "OVERVIEW-${YEAR}-DONE"
