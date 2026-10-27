#!/bin/bash
# EC2 user data (launch.sh fills the __X__ fields): outlines.py over units of tiles claimed from
# the S3 queue (pipeline/inference/aws/work_queue.py --outputs outlines), then terminate.
# Workers are one per physical core less two, capped at GB_PER_WORKER of RAM each, and split
# into slots of about 16. Each slot claims a unit, downloads the score COGs of its tiles that
# have no outline in S3 yet, and runs outlines.py on them. An uploader copies each finished tile
# to s3://BUCKET/PREFIX/outlines/{year}/{tile}.parquet and deletes the local copy. A failed run
# (a worker killed for memory breaks the whole pool) is retried once with a quarter of the
# workers. A spot notice or MAX_HOURS stops claiming; another box takes over the unit once the
# heartbeat is stale, and finished tiles are not redone.
set -euxo pipefail
exec > >(tee -a /var/log/ftw-outlines.log) 2>&1
BUCKET=__BUCKET__
P=__PREFIX__
SCORES=__SCORES__
SCORES_NO_SIGN=__SCORES_NO_SIGN__
YEARS='__YEARS__'
MAX_HOURS=__MAX_HOURS__
GB_PER_WORKER=__GB_PER_WORKER__
OUTLINE_ARGS='__OUTLINE_ARGS__'
export AWS_DEFAULT_REGION=__REGION__
S3=s3://$BUCKET/${P%/}
md() {
  local tok
  tok=$(curl -sX PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
  curl -sf -H "X-aws-ec2-metadata-token: $tok" "http://169.254.169.254/latest/meta-data/$1"
}
IID=$(md instance-id)
ITYPE=$(md instance-type)
finish() {
  echo dead | aws s3 cp - "$S3/hb/$IID.dead" --only-show-errors || true
  aws s3 cp /data/logs/ "$S3/logs/$IID/" --recursive --only-show-errors || true
  aws s3 cp /var/log/ftw-outlines.log "$S3/logs/$IID/bootstrap.log" --only-show-errors || true
  shutdown -h now
}
trap finish EXIT
(sleep "$(awk -v h="$MAX_HOURS" 'BEGIN {print int(h * 3600)}')"; echo "MAX_HOURS=$MAX_HOURS reached"
 touch /data/STOP; sleep 600; shutdown -h now) &

mkdir -p /data/scores /data/out /data/tmp /data/logs /data/todo
cd /data
aws s3 cp "$S3/bundle.tar.gz" . --only-show-errors
tar xzf bundle.tar.gz
command -v uv > /dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
export HOME=/root UV_CACHE_DIR=/data/uv-cache TMPDIR=/data/tmp PYTHONWARNINGS=ignore
echo "code $(cat /data/ftw/CODE_VERSION)"
PP=/data/ftw/pipeline/postprocessing
uv venv --python 3.13 /data/venv
uv pip install --python /data/venv/bin/python -r "$PP/requirements.txt" boto3
PY=(/data/venv/bin/python -u)
Q=("${PY[@]}" /data/ftw/pipeline/inference/aws/work_queue.py)
QA=(--bucket "$BUCKET" --prefix "$P")
NS=(); [[ $SCORES_NO_SIGN == 1 ]] && NS=(--no-sign-request)

(while true; do  # heartbeat; a spot notice (2 min warning) stops claiming at once
  echo "$ITYPE" | aws s3 cp - "$S3/hb/$IID" --only-show-errors || true
  if md spot/instance-action > /dev/null; then
    echo "spot interruption notice"; touch /data/STOP
    echo dead | aws s3 cp - "$S3/hb/$IID.dead" --only-show-errors || true
  fi
  sleep 30
done) &

# one worker per physical core (SMT siblings add little to single-threaded BoundaryVote)
cores=$(lscpu -p=core | grep -v '^#' | sort -u | wc -l)
mem_gb=$(awk '/MemTotal/ {print int($2 / 1048576)}' /proc/meminfo)
workers=$(( cores - 2 < mem_gb / GB_PER_WORKER ? cores - 2 : mem_gb / GB_PER_WORKER ))
(( workers > 0 )) || { echo "$ITYPE is too small for one worker"; exit 1; }
slots=$(( (workers + 15) / 16 ))
per=$(( workers / slots ))
echo "$ITYPE: $cores cores, $mem_gb GB, $slots slots of $per workers"

set +ex  # the loops below handle every failure; xtrace would flood the log

uploader() {  # a file on S3 means the tile is done
  while true; do
    busy=0
    while read -r f; do
      busy=1
      aws s3 cp "$f" "$S3/outlines/${f#/data/out/}" --only-show-errors && rm -f "$f"
    done < <(find /data/out -name '*.parquet' 2> /dev/null)
    (( busy )) || { [[ -f /data/FINISH ]] && return 0; sleep 10; }
  done
}
uploader &
UP=$!

run_unit() {  # outlines.py with $2 workers on the tiles of unit $1 that have no outline in S3
  local unit=$1 w=$2 rc=0 y tl t years
  "${Q[@]}" todo "${QA[@]}" --unit "$unit" --outputs outlines > "/data/todo/$unit.txt" || return 1
  years=$(awk '{print $1}' "/data/todo/$unit.txt" | sort -u)
  for y in $years; do
    tl=/data/todo/$unit.$y.tiles
    awk -v y="$y" '$1 == y {print $2}' "/data/todo/$unit.txt" > "$tl"
    # a COG that fails to download is reported by outlines.py as a missing input
    awk -v s="$SCORES/$y" -v d="/data/scores/$y" '{
      k = "zone=" substr($1, 1, 2) "/gzd=" substr($1, 1, 3) "/" $1 "/" $1 ".tif"
      print s "/" k; print d "/" k }' "$tl" |
      xargs -P 8 -n 2 aws s3 cp --only-show-errors "${NS[@]}" || true
    # shellcheck disable=SC2086  # OUTLINE_ARGS is a word list
    (cd "$PP" && AWS_NO_SIGN_REQUEST=YES "${PY[@]}" outlines.py --year "$y" --scores /data/scores \
      --out-root /data/out --index-dir /data/ftw/index --tile-list "$tl" --workers "$w" \
      --profile-out "/data/logs/profile_${unit}_${y}_$(date +%s).parquet" $OUTLINE_ARGS) \
      >> "/data/logs/$unit.log" 2>&1 || rc=$?
    while read -r t; do  # the uploader has shipped the tile (or it was never written)
      while [[ -e /data/out/$y/$t.parquet ]]; do sleep 5; done
      rm -rf "/data/scores/$y/zone=${t:0:2}/gzd=${t:0:3}/$t"
    done < "$tl"
  done
  return "$rc"
}

slot() {
  local unit rc n left
  while [[ ! -f /data/STOP ]]; do
    unit=$("${Q[@]}" claim "${QA[@]}" --owner "$IID" ${YEARS:+--years $YEARS}) || break
    rc=0; run_unit "$unit" "$per" || rc=$?
    n=$(cat "/data/todo/$unit.txt" 2> /dev/null | wc -l)
    if (( rc != 0 )) && [[ ! -f /data/STOP ]]; then
      echo "$unit: exit $rc, retrying the rest with $(( per / 4 > 0 ? per / 4 : 1 )) workers"
      rc=0; run_unit "$unit" $(( per / 4 > 0 ? per / 4 : 1 )) || rc=$?
    fi
    [[ -f /data/STOP ]] && break  # interrupted mid-unit: leave it claimed for another box
    left=$("${Q[@]}" todo "${QA[@]}" --unit "$unit" --outputs outlines | wc -l)
    (( left == 0 || rc != 0 )) || rc=1
    "${Q[@]}" "done" "${QA[@]}" --owner "$IID" --unit "$unit" --rc "$rc" --tiles "$n" --failed "$left"
    aws s3 cp /data/logs/ "$S3/logs/$IID/" --recursive --only-show-errors || true
  done
}
spids=()
for ((s = 0; s < slots; s++)); do slot & spids+=($!); done
for p in "${spids[@]}"; do wait "$p" || true; done
touch /data/FINISH
wait "$UP" || true
