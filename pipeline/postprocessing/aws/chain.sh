#!/bin/bash
# One year's chain after the outlines, on one machine: simplify -> merge -> fiboa_ranges local
# -> qa_vector. downstream.sh runs it on EC2; it runs the same way anywhere else.
#   chain.sh YEAR ROOT
# Reads ROOT/outlines/YEAR/{tile}.parquet and ROOT/keep_YEAR.txt (the year's expected tiles);
# writes ROOT/simplified, ROOT/merged, ROOT/fiboa/YEAR/zone=NN/utmNN.parquet and
# ROOT/qa/vector_YEAR.json, with DuckDB spill and range work under ROOT/tmp.
# Env: FTW_LAND_POLYGONS (sea_filter.py output, required), PY (python with requirements.txt,
# default python3), CPUS (default nproc), MEM_GB (default MemTotal), MERGE_ARGS (e.g.
# --allow-missing), SIMPLIFY_GB (memory per simplify worker, default 4).
set -euo pipefail
YEAR=${1:?year}
ROOT=$(realpath "${2:?root}")
HERE=$(cd "$(dirname "$0")/.." && pwd)
PY=${PY:-python3}
CPUS=${CPUS:-$(nproc)}
MEM_GB=${MEM_GB:-$(awk '/MemTotal/ {print int($2 / 1048576)}' /proc/meminfo)}
LAND=${FTW_LAND_POLYGONS:?set FTW_LAND_POLYGONS (sea_filter.py output)}
min() { echo $(( $1 < $2 ? $1 : $2 )); }
stamp() { echo "== $1 $(date -u +%H:%M:%S)"; }
mkdir -p "$ROOT/tmp" "$ROOT/qa"
: > "$ROOT/empty.txt"  # outlines.py writes a file for every tile, empty ones included

stamp simplify
"$PY" -u "$HERE/simplify_polygons.py" --year "$YEAR" --in-root "$ROOT/outlines" \
  --out-root "$ROOT/simplified" --workers "$(min "$CPUS" $(( MEM_GB / ${SIMPLIFY_GB:-4} )))"

stamp merge
# shellcheck disable=SC2086  # MERGE_ARGS is a word list
"$PY" -u "$HERE/merge_polygons.py" --year "$YEAR" --in-root "$ROOT/simplified" \
  --out-root "$ROOT/merged" --no-aux --tmp-dir "$ROOT/tmp/duckdb" \
  --keep-list "$ROOT/keep_$YEAR.txt" --empty-list "$ROOT/empty.txt" \
  --threads "$CPUS" --memory "$(( MEM_GB / 2 ))GB" ${MERGE_ARGS:-}

stamp fiboa
# a quarter of the jobs run plans at 14 GB of DuckDB each, a third run ranges at 5 GB
"$PY" -u "$HERE/fiboa_ranges.py" local --year "$YEAR" --in-root "$ROOT/merged" \
  --out-root "$ROOT/fiboa" --tmp-dir "$ROOT/tmp/duckdb" --work-root "$ROOT/tmp/fiboa-ranges" \
  --land-polygons "$LAND" --jobs "$(min "$CPUS" $(( MEM_GB / 5 )))"

stamp qa
"$PY" -u "$HERE/qa_vector.py" "$YEAR" --root "$ROOT/fiboa" --out "$ROOT/qa/vector_$YEAR.json" \
  --land-polygons "$LAND" --threads "$CPUS" --memory-limit "$(( MEM_GB / 2 ))GB"
stamp finished
