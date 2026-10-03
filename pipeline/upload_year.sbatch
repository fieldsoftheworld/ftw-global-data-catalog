#!/bin/bash
# Upload one year's PMTiles + a5 cells parquet to vector/{year}/ on Source Coop; record size + multihash.
# Usage: YEAR=2020 sbatch --export=ALL upload_year.sbatch
#SBATCH --account=bgtj-tgirails
#SBATCH --partition=cpu,cpu_amd
#SBATCH --exclude=rails[04-15]
#SBATCH --time=06:00:00
#SBATCH -c4
#SBATCH --mem=8G
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${YEAR:?}"
HERE=${SLURM_SUBMIT_DIR:-$PWD}
REPO=/u/isaaccorley/github/ftw-s2-quarterly
export UV_CACHE_DIR="$HOME/.cache/uv-ftw"
DST=s3://ftw/global-data-2e/vector/$YEAR
for kind in pmtiles cells; do
  if [[ $kind == pmtiles ]]; then f=$HERE/fields-$YEAR.pmtiles; else f=$HERE/cells_a5r7_$YEAR.parquet; fi
  [[ -s $f ]] || { echo "missing $f" >&2; exit 1; }
  (cd "$REPO" && uv run --no-sync python -u scripts/s3_put_retry.py --workers 16 "$f" "$DST/$(basename "$f")")
  h=$(sha256sum "$f" | cut -d' ' -f1)
  printf '{"size": %s, "checksum": "1220%s"}\n' "$(stat -c %s "$f")" "$h" > "$HERE/checksums/${kind}_$YEAR.json"
  echo "$kind $YEAR: $(cat "$HERE/checksums/${kind}_$YEAR.json")"
done
echo "UPLOAD-$YEAR-DONE"
