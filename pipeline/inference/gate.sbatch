#!/bin/bash
#SBATCH --job-name=year-gate
#SBATCH --account=bgtj-tgirails
#SBATCH --partition=cpu,cpu_amd
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=4G
#SBATCH --time=00:30:00
#SBATCH --output=year-gate-%j.out
#
# The raster gate of one year: gate.py scan, then gate.py summary over the scan and the
# validate.sbatch shards into $ROOT/qa/gate_$YEAR.json. Exits 0 only if the year passed, so an
# upload can depend on it with afterok. It reads tags only; the production gate took 20 s.
#
#   sbatch --dependency=afterany:<validate job> \
#     --export=ALL,YEAR=2025,ROOT=$OUT,INDEX_DIR=$INDEX pipeline/inference/gate.sbatch
# Optional: HISTORY (space-separated QA CSVs of other years), RELEASE (this year's release QA CSV).
set -uo pipefail
: "${YEAR:?}" "${ROOT:?stream.py --out-root}" "${INDEX_DIR:?tile_index_{year}.parquet}"
REPO="${REPO:-$SLURM_SUBMIT_DIR}"
PY="${PY:-$REPO/.venv/bin/python}"
cd "$REPO/pipeline/inference" || exit 1
# shellcheck disable=SC2086  # HISTORY holds several paths
"$PY" -u -W ignore gate.py scan --year "$YEAR" --root "$ROOT" --index-dir "$INDEX_DIR" \
  ${HISTORY:+--history $HISTORY} --out "$ROOT/qa/scan_$YEAR.csv"
"$PY" -u gate.py summary --year "$YEAR" --scan "$ROOT/qa/scan_$YEAR.csv" \
  --validate-dir "$ROOT/qa/validate_$YEAR" ${RELEASE:+--release "$RELEASE"} \
  --out "$ROOT/qa/gate_$YEAR.json"
