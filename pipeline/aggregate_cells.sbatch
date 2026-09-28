#!/bin/bash
# Per-year a5 cell aggregation for the low-zoom exploration layer.
#
# Produces cells_a5r${R}_${YEAR}.parquet with, per a5 cell:
#   count          number of fields
#   area_ha        total field area (whole hectares)
#   avg_confidence mean prediction confidence, 1dp (null where source has none)
#   pct_covered    100 * field area / a5 cell area (r7 cell = 2,075.4 km2),
#                  1dp — can exceed 100: a boundary field's full area counts
#                  in its home cell
#
#   YEAR=2025 R=7 MAX_AREA_KM2=350 sbatch --export=ALL aggregate_cells.sbatch
#   (IN=... to override the input; defaults to global${YEAR}_gp2.parquet)
#
# MAX_AREA_KM2 applies the giant-field cutoff at aggregate input (staging is
# unfiltered): fields above it are artifacts (>350 km2 is 100% junk; the
# largest legit complex is a 331 km2 Russian grain block).
#SBATCH --job-name=ftw-agg-cells
#SBATCH --partition=cpu
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

if [ -n "${MAX_AREA_KM2:-}" ]; then
  # area column is m2; keep the name distinct from any pre-dedupe *_le350 files
  FILT="${IN%.parquet}_le${MAX_AREA_KM2}.parquet"
  echo "== filter area <= ${MAX_AREA_KM2} km2 -> ${FILT}"
  # GP2 output so the same filtered file serves the tile_fields shard fleet
  # (native geo stats -> row-group pruning)
  [ -f "$FILT" ] || gpio extract geoparquet "$IN" "$FILT" \
    --where "area <= $((MAX_AREA_KM2 * 1000000))" --geoparquet-version 2.0
  IN="$FILT"
fi

echo "== a5 aggregate r${R} from ${IN}"
[ -f "$RAW" ] || gpio process aggregate a5 "$IN" "$RAW" \
  --resolution "$R" --metric "sum:area,avg:confidence" \
  --out-geometry polygon --geoparquet-version 2.0

echo "== add pct_covered"
# DuckDB output loses the 'geo' metadata key on rails; gpio convert restores it.
python3 add_coverage.py "$RAW" "$PLAIN"
gpio convert geoparquet "$PLAIN" "$OUT" --geoparquet-version 2.0
rm -f "$PLAIN"

python3 - "$OUT" <<'EOF'
import sys, duckdb
c = duckdb.connect(); c.execute("INSTALL spatial; LOAD spatial")
print(c.execute(f"DESCRIBE SELECT * FROM '{sys.argv[1]}'").df().to_string(), flush=True)
print(c.execute(f"""SELECT count(*) cells, sum(count) fields, round(sum(area_ha)/1e8,2) mkm2,
    round(avg(pct_covered),2) mean_pct, round(max(pct_covered),1) max_pct
    FROM '{sys.argv[1]}'""").df().to_string(), flush=True)
EOF
echo "CELLS-DONE"
