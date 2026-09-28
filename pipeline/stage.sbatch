#!/bin/bash
# Stage one beta year (54 UTM-zone files, all columns except bbox) and merge
# into one GeoParquet 2.0 file with native geo stats (enables tylertoo's
# row-group pruning).
#
#   cp pipeline/* ~/ftw-beta-pipeline/ && cd ~/ftw-beta-pipeline
#   YEAR=2025 sbatch --export=ALL stage.sbatch
#SBATCH --job-name=ftw-beta-stage
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
export PATH="$HOME/ftw-us-tiles/venv/bin:/u/cholmes/micromamba/envs/ftw/bin:$PATH"
export TMPDIR="$PWD/tmp-stage"   # /u spill — /tmp is tmpfs, counts against the cgroup
mkdir -p "$TMPDIR"

echo "== stage $YEAR per zone (resumable; no dedupe/cutoff needed — measured)"
python3 stage_global.py --year "$YEAR"

echo "== merge $YEAR"
# gpio extract keeps GeoParquet metadata intact across the merge (a plain
# DuckDB COPY dropped the 'geo' key on compute nodes before).
[ -f "global${YEAR}.parquet" ] || gpio extract "staged/${YEAR}/*.parquet" "global${YEAR}.parquet"
echo "== convert $YEAR to GeoParquet 2.0 (native geo stats -> row-group pruning)"
[ -f "global${YEAR}_gp2.parquet" ] || gpio convert geoparquet \
    "global${YEAR}.parquet" "global${YEAR}_gp2.parquet" --geoparquet-version 2.0

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
