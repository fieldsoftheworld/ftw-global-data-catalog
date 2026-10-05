#!/bin/bash
# Upload one year's PMTiles + a5 cells parquet to vector/{year}/ on Source Coop; record size + multihash.
# The size + multihash go into staging-data/checksums/tiles_meta.json, which is what
# tools/build_vector_items.py reads when it stamps file:size/file:checksum on the assets.
# Usage: YEAR=2020 sbatch --export=ALL upload_year.sbatch
#        (optional: PY=/path/to/python PROFILE=name META=/path/to/tiles_meta.json)
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
# whole file) and gives the object the Content-Type tools/publish.py assigns the suffix.
# PY needs boto3; PROFILE is an AWS profile with Source Coop credentials ('' = env).
PY=${PY:-python3}
PROFILE=${PROFILE:-source-coop}
# tools/build_vector_items.py reads exactly one file for these numbers,
# staging-data/checksums/tiles_meta.json, keyed {kind}_{year}. Writing them anywhere else
# means a hand-merge, and a missed hand-merge drops file:size/file:checksum from the
# regenerated collection, so this job merges straight into the file the builder reads.
# Submitted from the repo's pipeline/ dir the default resolves to it; else set META.
META=${META:-$HERE/../staging-data/checksums/tiles_meta.json}
mkdir -p "$HERE/checksums"
DST=s3://ftw/global-data-2e/vector/$YEAR
for kind in pmtiles cells; do
  if [[ $kind == pmtiles ]]; then f=$HERE/fields-$YEAR.pmtiles; else f=$HERE/cells_a5r7_$YEAR.parquet; fi
  [[ -s $f ]] || { echo "missing $f" >&2; exit 1; }
  "$PY" -u "$HERE/s3_put_retry.py" --profile "$PROFILE" --workers 16 "$f" "$DST/$(basename "$f")"
  h=$(sha256sum "$f" | cut -d' ' -f1)
  printf '{"size": %s, "checksum": "1220%s"}\n' "$(stat -c %s "$f")" "$h" > "$HERE/checksums/${kind}_$YEAR.json"
  "$PY" - "$META" "${kind}_$YEAR" "$(stat -c %s "$f")" "1220$h" <<'PY'
import json, pathlib, sys
meta, key, size, checksum = sys.argv[1:5]
path = pathlib.Path(meta)
path.parent.mkdir(parents=True, exist_ok=True)
entries = json.loads(path.read_text()) if path.exists() else {}
entries[key] = {"size": int(size), "checksum": checksum}
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(entries, indent=1) + "\n")
tmp.replace(path)  # atomic: a crash mid-write must not truncate the file every year shares
print(f"{key} -> {path}")
PY
  echo "$kind $YEAR: $(cat "$HERE/checksums/${kind}_$YEAR.json")"
done
echo "UPLOAD-$YEAR-DONE"
