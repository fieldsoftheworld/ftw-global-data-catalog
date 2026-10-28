#!/bin/bash
# EC2 user data (launch.sh downstream fills the __X__ fields): one year's chain after the
# outlines on this box, then terminate. The year's outlines (PREFIX/outlines/YEAR/) and its
# expected tiles (the tiles of the units PREFIX/units/YEAR_*) come down, chain.sh runs simplify,
# merge, fiboa_ranges local and qa_vector, and the zone files go to PREFIX/fiboa/YEAR/, merge's
# _summary.json to PREFIX/merged/YEAR/ and the QA report to PREFIX/qa/vector_YEAR.json.
# PREFIX/downstream/YEAR.done holds the exit code. The box stops after MAX_HOURS whatever happens.
set -euxo pipefail
exec > >(tee -a /var/log/ftw-downstream.log) 2>&1
BUCKET=__BUCKET__
P=__PREFIX__
YEAR=__YEAR__
MAX_HOURS=__MAX_HOURS__
MERGE_ARGS='__MERGE_ARGS__'
export AWS_DEFAULT_REGION=__REGION__
export AWS_REGION=$AWS_DEFAULT_REGION  # s5cmd
S3=s3://$BUCKET/${P%/}
shutdown -h +$(( MAX_HOURS * 60 )) || true
HOST=$(hostname -s)
log() { aws s3 cp /var/log/ftw-downstream.log "$S3/logs/downstream-$YEAR-$HOST.log" --only-show-errors || true; }
# shellcheck disable=SC2154  # rc is assigned inside the trap
trap 'rc=$?; log; echo "$rc" | aws s3 cp - "$S3/downstream/$YEAR.done" --only-show-errors; shutdown -h now' EXIT

mkdir -p /data/tmp /data/units && cd /data
aws s3 cp "$S3/bundle.tar.gz" . --only-show-errors
tar xzf bundle.tar.gz
command -v uv > /dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
# s5cmd: aws s3 sync was too slow for a year's ~7,500 outline files
curl -fsSL -o s5cmd.tar.gz https://github.com/peak/s5cmd/releases/download/v2.3.0/s5cmd_2.3.0_Linux-64bit.tar.gz
echo "de0fdbfa3aceae55e069ba81a0fc17b2026567637603734a387b2fca06c299b4  s5cmd.tar.gz" | sha256sum -c
tar xzf s5cmd.tar.gz -C /usr/local/bin s5cmd
export HOME=/root UV_CACHE_DIR=/data/uv-cache TMPDIR=/data/tmp PYTHONWARNINGS=ignore
echo "code $(cat /data/ftw/CODE_VERSION)"
PP=/data/ftw/pipeline/postprocessing
uv venv --python 3.13 /data/venv
uv pip install --python /data/venv/bin/python -r "$PP/requirements.txt"
(while true; do sleep 300; log; done) &

R=/data/run
mkdir -p "$R"
s5cmd --numworkers 128 sync "$S3/outlines/$YEAR/*" "$R/outlines/$YEAR/"
aws s3 cp "$S3/units/" /data/units/ --recursive --exclude '*' --include "${YEAR}_*" --only-show-errors
cat /data/units/"${YEAR}"_*.txt | awk '{print $2}' | sort -u > "$R/keep_$YEAR.txt"
echo "outlines: $(find "$R/outlines/$YEAR" -name '*.parquet' | wc -l) of $(wc -l < "$R/keep_$YEAR.txt") expected tiles"
aws s3 cp "$S3/land_polygons.parquet" /data/land_polygons.parquet --only-show-errors

FTW_LAND_POLYGONS=/data/land_polygons.parquet PY=/data/venv/bin/python MERGE_ARGS=$MERGE_ARGS \
  bash "$PP/aws/chain.sh" "$YEAR" "$R"

s5cmd --numworkers 64 sync "$R/fiboa/$YEAR/*" "$S3/fiboa/$YEAR/"
aws s3 cp "$R/merged/$YEAR/_summary.json" "$S3/merged/$YEAR/_summary.json" --only-show-errors
aws s3 cp "$R/qa/vector_$YEAR.json" "$S3/qa/vector_$YEAR.json" --only-show-errors
echo "fiboa: $(find "$R/fiboa/$YEAR" -name 'utm*.parquet' | wc -l) zone files"
