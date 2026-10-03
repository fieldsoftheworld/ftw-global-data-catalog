#!/bin/bash
# Upload one year's PMTiles + a5 cells parquet to vector/{year}/ on Source Coop; record size + multihash.
# Usage: YEAR=2020 sbatch --export=ALL upload_year.sbatch   (optional: PY=/path/to/python PROFILE=name)
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
# s3_put_retry.py retries each multipart part (the proxy answers 520 and aws s3 cp restarts the
# whole file). PY needs boto3; PROFILE is an AWS profile with Source Coop credentials ('' = env).
PY=${PY:-python3}
PROFILE=${PROFILE:-source-coop}
DST=s3://ftw/global-data-2e/vector/$YEAR
for kind in pmtiles cells; do
  if [[ $kind == pmtiles ]]; then f=$HERE/fields-$YEAR.pmtiles; else f=$HERE/cells_a5r7_$YEAR.parquet; fi
  [[ -s $f ]] || { echo "missing $f" >&2; exit 1; }
  "$PY" -u "$HERE/s3_put_retry.py" --profile "$PROFILE" --workers 16 "$f" "$DST/$(basename "$f")"
  h=$(sha256sum "$f" | cut -d' ' -f1)
  printf '{"size": %s, "checksum": "1220%s"}\n' "$(stat -c %s "$f")" "$h" > "$HERE/checksums/${kind}_$YEAR.json"
  echo "$kind $YEAR: $(cat "$HERE/checksums/${kind}_$YEAR.json")"
done
echo "UPLOAD-$YEAR-DONE"
