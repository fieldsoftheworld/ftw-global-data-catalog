#!/bin/bash
# Per-year a5 cell aggregation for the low-zoom exploration layer (2e).
#
# Produces cells_a5r${R}_${YEAR}.parquet with, per a5 cell:
#   count             number of parcels
#   area_ha           total field area (whole hectares)
#   avg_score         mean parcel score (field probability x 100), 1dp
#   pct_covered       100 * field area / a5 cell area (r7 cell = 2,075.5 km2),
#                     1dp — can exceed 100: a boundary parcel's full area
#                     counts in its home cell
#
#   YEAR=2025 R=7 sbatch --export=ALL aggregate_cells.sbatch
#   (IN=... to override the input; defaults to global${YEAR}_gp2.parquet)
#
# No MAX_AREA_KM2 filter: the 2e upstream already removes parcels > 5 km2
# (measured max 5.007 km2 across all 2025 zones).
#SBATCH --job-name=ftw-2e-agg-cells
#SBATCH --partition=cpu,cpu_amd
#SBATCH --account=bgtj-tgirails
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=192G
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}"
R="${R:-7}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
IN="${IN:-$PWD/global${YEAR}_gp2.parquet}"
export PATH="$HOME/ftw-us-tiles/venv/bin:/u/cholmes/micromamba/envs/ftw/bin:$PATH"
export TMPDIR="$PWD/tmp-cells-${YEAR}"   # /u spill — /tmp is tmpfs
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

RAW="cells_a5r${R}_${YEAR}_raw.parquet"
PLAIN="cells_a5r${R}_${YEAR}_plain.parquet"
OUT="cells_a5r${R}_${YEAR}.parquet"

echo "== a5 aggregate r${R} from ${IN}"
# Metric naming verified on gpio 1.5.0: outputs count, sum_metrics:area,
# avg_score.
[ -f "$RAW" ] || gpio process aggregate a5 "$IN" "$RAW" \
  --resolution "$R" \
  --metric "sum:metrics:area,avg:score" \
  --out-geometry polygon --geoparquet-version 2.0

echo "== add pct_covered + final names"
# DuckDB output loses the 'geo' metadata key on rails; gpio convert restores it.
python3 add_coverage.py "$RAW" "$PLAIN"
gpio convert geoparquet "$PLAIN" "$OUT" --geoparquet-version 2.0
rm -f "$PLAIN"

python3 - "$OUT" <<'EOF'
import sys, duckdb
c = duckdb.connect(); c.execute("INSTALL spatial; LOAD spatial")
print(c.execute(f"DESCRIBE SELECT * FROM '{sys.argv[1]}'").df().to_string(), flush=True)
print(c.execute(f"""SELECT count(*) cells, sum(count) parcels,
    round(sum(area_ha)/1e8,2) mkm2, round(avg(pct_covered),2) mean_pct,
    round(max(pct_covered),1) max_pct, round(avg(avg_score),1) mean_score
    FROM '{sys.argv[1]}'""").df().to_string(), flush=True)
# Distributions for the style-plan checkpoint: quantiles per styled metric.
for col in ("count", "area_ha", "pct_covered", "avg_score"):
    q = c.execute(f"""SELECT list_transform(
            quantile_cont({col}, [0.05,0.25,0.5,0.75,0.9,0.99]),
            x -> round(x::DOUBLE, 3)) FROM '{sys.argv[1]}'""").fetchone()[0]
    print(f"quantiles {col} [p5,p25,p50,p75,p90,p99]: {q}", flush=True)
EOF
echo "CELLS-DONE"
