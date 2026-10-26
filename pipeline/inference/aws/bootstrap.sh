#!/bin/bash
# EC2 user data (launch.sh fills the __X__ fields): score inference on every GPU of the box, then
# terminate. The bundle and pinned environment are installed, the golden self-test runs on every
# GPU (any failure terminates the box before it takes work), then one worker per GPU claims
# units from the S3 queue (work_queue.py) and runs stream.py, which writes finished COGs under
# /data/out. An uploader copies each sidecar and then its COG to s3://BUCKET/PREFIX at the same
# key and deletes the local copy. A spot notice or MAX_HOURS stops the box; another box takes over its unit once
# the heartbeat is stale, and finished tiles are not redone.
set -euxo pipefail
exec > >(tee -a /var/log/ftw-gpu.log) 2>&1
BUCKET=__BUCKET__
P=__PREFIX__
GPU_MATCH='__GPU_MATCH__'
YEARS='__YEARS__'
MAX_HOURS=__MAX_HOURS__
MAX_UNITS=__MAX_UNITS__
PREFETCH=__PREFETCH__
TILE_WORKERS=__TILE_WORKERS__
WRITERS=__WRITERS__
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
  aws s3 cp /var/log/ftw-gpu.log "$S3/logs/$IID/bootstrap.log" --only-show-errors || true
  shutdown -h now
}
trap finish EXIT
(sleep "$(awk -v h="$MAX_HOURS" 'BEGIN {print int(h * 3600)}')"; echo "MAX_HOURS=$MAX_HOURS reached"
 touch /data/STOP; sleep 600; shutdown -h now) &

mkdir -p /data/out /data/tmp /data/logs /data/todo
cd /data
aws s3 cp "$S3/bundle.tar.gz" . --only-show-errors
tar xzf bundle.tar.gz
aws s3 sync "$S3/golden/" /data/golden/ --only-show-errors
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
export HOME=/root UV_CACHE_DIR=/data/uv-cache TMPDIR=/data/tmp PYTHONWARNINGS=ignore
FTW_CODE_VERSION=$(cat /data/ftw/CODE_VERSION)  # no .git in the bundle
export FTW_CODE_VERSION
unset LD_LIBRARY_PATH  # the wheels supply CUDA and cuDNN; the AMI's copies must not load
INF=/data/ftw/pipeline/inference
uv venv --python 3.14 /data/venv
uv pip sync --python /data/venv/bin/python "$INF/requirements.lock"
PY=(/data/venv/bin/python -u)
Q=("${PY[@]}" "$INF/aws/work_queue.py")
MODEL=$(ls /data/golden/model/*.onnx)

(while true; do  # heartbeat; a spot notice (2 min warning) stops claiming at once
  echo "$ITYPE" | aws s3 cp - "$S3/hb/$IID" --only-show-errors || true
  if md spot/instance-action >/dev/null; then
    echo "spot interruption notice"; touch /data/STOP
    echo dead | aws s3 cp - "$S3/hb/$IID.dead" --only-show-errors || true
  fi
  sleep 30
done) &

mapfile -t GPU_IDS < <(nvidia-smi --query-gpu=index --format=csv,noheader)
name=$(nvidia-smi -i 0 --query-gpu=name --format=csv,noheader)
[[ $name == *"$GPU_MATCH"* ]] || { echo "GPU $name does not match $GPU_MATCH"; exit 1; }
drv=$(nvidia-smi -i 0 --query-gpu=driver_version --format=csv,noheader | cut -d. -f1)
(( drv >= 580 )) || { echo "driver $drv < 580: the CUDA 13 wheels need >= 580"; exit 1; }

# self-test on every GPU at once, against the reference cluster's H100 outputs
against=$(find /data/golden/gpu_outputs -maxdepth 1 -name '*H100*_window31' | sort | head -n 1)
pids=()
for g in "${GPU_IDS[@]}"; do
  (cd "$INF" && CUDA_VISIBLE_DEVICES=$g "${PY[@]}" selftest.py check --root /data/golden \
    --onnx "$MODEL" --full-tile --out "/data/selftest/gpu$g" ${against:+--against "$against"}) \
    > "/data/logs/selftest_gpu$g.log" 2>&1 &
  pids+=($!)
done
ok=1
for p in "${pids[@]}"; do wait "$p" || ok=0; done
grep -h -E "^(PASS|FAIL|ALL PASS|FAILED)" /data/logs/selftest_gpu*.log | sort | uniq -c || true
for d in /data/selftest/*/; do
  aws s3 cp "$d/check.json" "$S3/selftest/$IID/$(basename "$d").json" --only-show-errors || true
done
[[ $ok == 1 ]] || { echo "SELF-TEST FAILED on $ITYPE ($name): no work taken"; exit 1; }
echo "$ITYPE $name" | aws s3 cp - "$S3/selftest/$IID/ALL_PASS" --only-show-errors

set +ex  # the loops below handle every failure; xtrace would flood the log

uploader() {  # sidecar first, then the COG: a COG on S3 means the tile is done
  while true; do
    busy=0
    while read -r cog; do
      busy=1
      rel=${cog#/data/out/}
      y=$(cut -d/ -f2 <<< "$rel"); t=$(basename "$cog" .tif)
      side=/data/out/_qa/$y/$t.json
      aws s3 cp "$side" "$S3/_qa/$y/$t.json" --only-show-errors &&
        aws s3 cp "$cog" "$S3/$rel" --only-show-errors &&
        rm -f "$cog" "$side"
    done < <(find /data/out/raster -name '*.tif' 2>/dev/null)
    (( busy )) || { [[ -f /data/FINISH ]] && return 0; sleep 10; }
  done
}
uploader &
UP=$!

worker() {
  local g=$1 n=0 unit rc years
  while [[ ! -f /data/STOP ]] && (( MAX_UNITS == 0 || n < MAX_UNITS )); do
    unit=$("${Q[@]}" claim --bucket "$BUCKET" --prefix "$P" --owner "$IID" ${YEARS:+--years $YEARS}) || break
    n=$((n + 1)); rc=0
    "${Q[@]}" todo --bucket "$BUCKET" --prefix "$P" --unit "$unit" > "/data/todo/$unit.txt"
    years=$(awk '{print $1}' "/data/todo/$unit.txt" | sort -u)
    for y in $years; do
      awk -v y="$y" '$1 == y {print $2}' "/data/todo/$unit.txt" > "/data/todo/$unit.$y.tiles"
      (cd "$INF" && CUDA_VISIBLE_DEVICES=$g "${PY[@]}" stream.py --year "$y" \
        --tiles-file "/data/todo/$unit.$y.tiles" --shard 0 --num-shards 1 \
        --index-dir /data/ftw/index --onnx "$MODEL" --out-root /data/out \
        --require-gpu "$GPU_MATCH" --prefetch "$PREFETCH" --tile-workers "$TILE_WORKERS" \
        --writers "$WRITERS" --stats-file "/data/logs/stats_${IID}_gpu$g.jsonl") \
        >> "/data/logs/infer_gpu$g.log" 2>> "/data/logs/infer_gpu$g.err" || rc=$?
    done
    while read -r y t; do  # the unit is done once the uploader has shipped its tiles
      while [[ -n $(find "/data/out/raster/$y" -name "$t.tif" 2>/dev/null) ]]; do sleep 5; done
    done < "/data/todo/$unit.txt"
    [[ -f /data/STOP ]] && break  # interrupted mid-unit: leave it claimed for another box
    left=$("${Q[@]}" todo --bucket "$BUCKET" --prefix "$P" --unit "$unit" | wc -l)
    (( left == 0 || rc != 0 )) || rc=1
    "${Q[@]}" "done" --bucket "$BUCKET" --prefix "$P" --owner "$IID" --unit "$unit" --rc "$rc" \
      --tiles "$(wc -l < "/data/todo/$unit.txt")" --failed "$left"
    aws s3 cp /data/logs/ "$S3/logs/$IID/" --recursive --only-show-errors || true
  done
}
wpids=()
for g in "${GPU_IDS[@]}"; do worker "$g" & wpids+=($!); done
for p in "${wpids[@]}"; do wait "$p" || true; done
touch /data/FINISH
wait "$UP" || true
