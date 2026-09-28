#!/bin/bash
# Tile a per-year a5 cells file into the low-zoom exploration PMTiles:
# a single "cells" layer, every cell verbatim at every zoom (no thinning,
# no drops), z0-8. Prints a per-zoom tile-weight report afterwards so the
# <=600 KB @ z2+ budget can be checked.
#
#   YEAR=2025 R=7 sbatch --export=ALL tile_cells.sbatch
#   (IN=... to override; defaults to cells_a5r${R}_${YEAR}.parquet)
#SBATCH --job-name=ftw-tile-cells
#SBATCH --partition=cpu
#SBATCH --account=bgtj-tgirails
#SBATCH --time=12:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=192G
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${YEAR:?set YEAR via --export=ALL,YEAR=..}"
R="${R:-7}"
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}"
IN="${IN:-$PWD/cells_a5r${R}_${YEAR}.parquet}"
OUT="${OUT:-fields-${YEAR}-a5r${R}.pmtiles}"
TT="${TT:-$HOME/tylertoo-src/target/release/tylertoo}"
export TMPDIR="$PWD/tmp-tile-cells-${YEAR}"   # /u spill — /tmp is tmpfs
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT
export RUST_LOG=info

# (tylertoo `tiles` has no -f; remove any stale output ourselves)
rm -f "$OUT"
# a5_cell (a 19-digit id per feature) stays in the parquet but out of the
# tiles — together with the rounding in add_coverage.py this is what holds
# the worst z2 tile under the 600 KB budget (measured 543 KB, global 2025).
"$TT" tiles "$IN" "$OUT" \
  --min-zoom 0 --max-zoom 8 --layer-name cells --verbatim \
  --exclude-property a5_cell \
  --profile bounded -v

ls -la "$OUT"
sha256sum "$OUT"
echo "== per-zoom tile weights"
~/ftw-us-tiles/venv/bin/python3 tile_weights.py "$OUT"
echo "TILE-CELLS-DONE"
