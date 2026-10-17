#!/bin/bash
# Stage one 2e year (54 UTM-zone files, all columns except bbox) and merge
# into one GeoParquet 2.0 file with native geo stats (enables tylertoo's
# row-group pruning).
#
#   cp pipeline/* ~/ftw-2e-pipeline/ && cd ~/ftw-2e-pipeline
#   YEAR=2025 sbatch --export=ALL stage.sbatch
#SBATCH --job-name=ftw-2e-stage
#SBATCH --partition=cpu,cpu_amd
#SBATCH --account=bgtj-tgirails
#SBATCH --time=12:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=120G
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
export PATH="$HOME/ftw-us-tiles/venv/bin:$PATH"  # pinned to this venv only (gpio 1.6.0, duckdb 1.5.6)
# Per-year temp isolation: concurrent year jobs share this submit dir, and
# both DuckDB spill and gpio's cwd-relative .tmp/ collide across jobs
# (observed: three concurrent converts corrupted each other's temp storage).
export TMPDIR="$PWD/tmp-stage-${YEAR}"   # /u spill — /tmp is tmpfs
mkdir -p "$TMPDIR"
CONVERT_DIR="$PWD/convert-${YEAR}"
mkdir -p "$CONVERT_DIR"

echo "== stage $YEAR per zone (resumable; no dedupe/cutoff needed — measured)"
python3 stage_global.py --year "$YEAR"

echo "== merge $YEAR straight to GeoParquet 2.0 (one pass, native geo stats)"
# gpio >= 1.6.0: extract merges the glob AND writes 2.0 with native
# row-group stats in one memory-bounded pass (#1154 funnel) — the separate
# whole-file convert this script used to run is gone (gpio#1151 discussion).
[ -f "global${YEAR}_gp2.parquet" ] || gpio extract "staged/${YEAR}/*.parquet" \
    "global${YEAR}_gp2.parquet" --geoparquet-version 2.0

python3 - "$YEAR" <<'EOF'
import sys, duckdb
year = sys.argv[1]
c = duckdb.connect(); c.execute("INSTALL spatial; LOAD spatial")
n, cols = c.execute(
    f"SELECT count(*), (SELECT count(*) FROM (DESCRIBE SELECT * FROM 'global{year}_gp2.parquet'))"
    f" FROM 'global{year}_gp2.parquet'").fetchone()
print(f"global {year}: {n:,} features, {cols} columns", flush=True)
EOF
echo "STAGE-DONE"
