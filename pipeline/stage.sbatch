#!/bin/bash
# Stage the global FTW predictions (both years, deduped) and merge each year
# into one GeoParquet 2.0 file with native geo stats (enables tylertoo's
# row-group pruning).
#
#   rsync -av scripts/tiles/ rails:ftw-pipeline/
#   ssh rails 'cd ~/ftw-pipeline && sbatch stage.sbatch'
#
# Optional: MAX_AREA_KM2=100 sbatch --export=ALL stage.sbatch
#SBATCH --job-name=ftw-stage
#SBATCH --partition=cpu
#SBATCH --account=bgtj-tgirails
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=120G
#SBATCH --output=%x-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
export PATH="$HOME/ftw-us-tiles/venv/bin:/u/cholmes/micromamba/envs/ftw/bin:$PATH"
export TMPDIR="$PWD/tmp-stage"   # /u spill — /tmp is tmpfs, counts against the cgroup
mkdir -p "$TMPDIR"

echo "== stage per country/year (resumable, deduped per #9)"
python3 stage_global.py ${MAX_AREA_KM2:+--max-area-km2 "$MAX_AREA_KM2"}

for Y in 2024 2025; do
  echo "== merge $Y"
  # gpio extract keeps GeoParquet metadata intact across the merge (a plain
  # DuckDB COPY dropped the 'geo' key on compute nodes before).
  [ -f "global${Y}.parquet" ] || gpio extract "staged/${Y}/*.parquet" "global${Y}.parquet"
  echo "== convert $Y to GeoParquet 2.0 (native geo stats -> row-group pruning)"
  [ -f "global${Y}_gp2.parquet" ] || gpio convert geoparquet \
      "global${Y}.parquet" "global${Y}_gp2.parquet" --geoparquet-version 2.0
done

python3 - <<'EOF'
import duckdb
c = duckdb.connect(); c.execute("INSTALL spatial; LOAD spatial")
for y in (2024, 2025):
    n, conf = c.execute(f"SELECT count(*), count(confidence) FROM 'global{y}_gp2.parquet'").fetchone()
    print(f"global {y}: {n:,} features, {conf:,} with confidence", flush=True)
EOF
echo "STAGE-DONE"
