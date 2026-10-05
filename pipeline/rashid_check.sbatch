#!/bin/bash
#SBATCH --job-name=rashid-check
#SBATCH --account=bgtj-tgirails
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=400G
#SBATCH --time=06:00:00
#SBATCH --output=pipeline/rashid-check-%j.out
#
# The Portolan conformance gate over the full raster tree, on a compute node.
#
# `rashid check` is single-threaded, but on this catalog it is neither quick
# nor small: 71,088 STAC objects, of which 67,197 are items, and rashid holds
# every one of them in memory as parsed JSON while its containment helpers
# rescan the whole graph once per catalog. A login-node run was killed after
# 39 minutes. So it runs here, where it can have real memory and real time,
# and `sacct -j $SLURM_JOB_ID --format=MaxRSS,Elapsed` reports what it used.
#
# No network is needed: --no-data skips the byte checks and the schemas are
# bundled, which is what lets this run on a compute node at all.
#
#   sbatch pipeline/rashid_check.sbatch
set -uo pipefail

REPO="${REPO:-$SLURM_SUBMIT_DIR}"
VENV="${VENV:-/u/cholmes/ftw-global-data-catalog/.venv}"
OUT="${OUT:-$REPO/pipeline/rashid-check-$SLURM_JOB_ID.json}"

export TMPDIR="${TMPDIR:-/u/cholmes/tmp}"
mkdir -p "$TMPDIR"

echo "host      $(hostname)"
echo "repo      $REPO"
echo "objects   $(find "$REPO/catalog" -name '*.json' | wc -l) json file(s)"
echo "rashid    $("$VENV/bin/rashid" --version)"

start=$(date +%s)
/usr/bin/time -v "$VENV/bin/rashid" check "$REPO/catalog" \
    --no-data --json --all > "$OUT" 2> "$OUT.time"
rc=$?
end=$(date +%s)

echo "exit      $rc"
echo "elapsed   $((end - start)) s"
grep -E "Maximum resident set size|Elapsed \(wall" "$OUT.time" || true
echo "report    $OUT ($(stat -c %s "$OUT" 2>/dev/null || echo 0) bytes)"
exit "$rc"
