#!/bin/bash
# Score inference on EC2 GPU boxes (bootstrap.sh runs on each). From the repo root:
#   launch.sh setup            check the bucket and instance profile; upload the golden set
#   launch.sh bundle [REF]     code of git REF (default HEAD) + indexes -> PREFIX/bundle.tar.gz
#   launch.sh units YEAR...    queue units {year}_{shard} (NSHARDS, default 256)
#   launch.sh up N             N boxes of TYPE, one run-instances each, over the subnets
#   launch.sh fleet GPUS       an EC2 Fleet of ~GPUS GPUs of TYPE (price-capacity-optimized)
#   launch.sh status | down    queue and boxes | terminate every running box
#   launch.sh quotas           P and G vCPU quotas
# Required env: FTW_AWS_BUCKET (us-west-2), FTW_AWS_ROLE (instance profile with read/write on the
# bucket), FTW_AWS_SUBNETS ("us-west-2a=subnet-... us-west-2b=subnet-..."), FTW_GOLDEN (self-test
# root, selftest.py), FTW_INDEX_DIR (tile_index_{year}.parquet, pipeline/mosaics/indexes.py).
# Optional: AWS_PROFILE, REGION (us-west-2), PREFIX (infer/), TYPE (p5.48xlarge), YEARS (claim
# only these), MAX_HOURS (24), MAX_UNITS (0 = no cap), WRITERS (3), ONDEMAND=1, DRYRUN=1.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export AWS_DEFAULT_REGION=${REGION:-us-west-2}
BUCKET=${FTW_AWS_BUCKET:?set FTW_AWS_BUCKET}
ROLE=${FTW_AWS_ROLE:?set FTW_AWS_ROLE}
PREFIX=${PREFIX:-infer/}
TYPE=${TYPE:-p5.48xlarge}
NAME=ftw-gpu
INF=pipeline/inference
AMI_PARAM=/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-amazon-linux-2023/latest/ami-id
declare -A SUBNET=()
for kv in ${FTW_AWS_SUBNETS:-}; do SUBNET[${kv%%=*}]=${kv#*=}; done
DR=(); [[ -n ${DRYRUN:-} ]] && DR=(--dry-run)

# GPU name substring (stream.py --require-gpu), GPUs per box, prefetch, tile workers per GPU.
# Prefetch 3 peaks at ~48 GiB of host RAM per GPU worker.
gpu_spec() {
  case $1 in
    p5.48xlarge) echo "H100 8 3 3" ;;
    p5e.48xlarge | p5en.48xlarge) echo "H200 8 3 3" ;;
    p6-b200.48xlarge) echo "B200 8 3 3" ;;
    p4de.24xlarge) echo "A100 8 3 3" ;;  # the 40 GB A100 (p4d) cannot hold a tile
    *) echo "unsupported type $1 (needs 80 GB GPUs)" >&2; exit 1 ;;
  esac
}

userdata() {  # PREFIX, type, max units per box
  local spec
  read -r -a spec <<< "$(gpu_spec "$2")"
  sed -e "s|__BUCKET__|$BUCKET|" -e "s|__PREFIX__|$1|" -e "s|__GPU_MATCH__|${spec[0]}|" \
    -e "s|__YEARS__|${YEARS:-}|" -e "s|__MAX_HOURS__|${MAX_HOURS:-24}|" -e "s|__MAX_UNITS__|$3|" \
    -e "s|__PREFETCH__|${spec[2]}|" -e "s|__TILE_WORKERS__|${spec[3]}|" \
    -e "s|__WRITERS__|${WRITERS:-3}|" -e "s|__REGION__|$AWS_DEFAULT_REGION|" "$INF/aws/bootstrap.sh"
}

ami() { aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text; }

run_one() {  # one box of type $1 with user data file $2, trying the subnets in turn
  local market=(--instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}')
  [[ ${ONDEMAND:-0} == 1 ]] && market=()
  for az in "${!SUBNET[@]}"; do
    if aws ec2 run-instances "${DR[@]}" --image-id "$(ami)" --instance-type "$1" --count 1 \
      --subnet-id "${SUBNET[$az]}" --iam-instance-profile Name="$ROLE" "${market[@]}" \
      --instance-initiated-shutdown-behavior terminate --metadata-options HttpTokens=required \
      --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=300,VolumeType=gp3,Iops=6000,Throughput=500,DeleteOnTermination=true}' \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=project,Value=ftw},{Key=queue,Value=$PREFIX}]" \
        "ResourceType=volume,Tags=[{Key=project,Value=ftw}]" \
      --user-data "file://$2" --query 'Instances[].[InstanceId,InstanceType,Placement.AvailabilityZone]' \
      --output text 2> "$2.err"; then
      return 0
    fi
    grep -q DryRunOperation "$2.err" && { echo "dry run ok: $1 in $az"; return 0; }
    echo "$1 $az: $(tr '\n' ' ' < "$2.err" | tail -c 300)" >&2
  done
  return 1
}

case "${1:?setup | bundle | units | up | fleet | status | down | quotas}" in
  setup)
    aws s3api head-bucket --bucket "$BUCKET"
    aws iam get-instance-profile --instance-profile-name "$ROLE" --query InstanceProfile.Arn --output text
    (( ${#SUBNET[@]} )) || { echo "set FTW_AWS_SUBNETS" >&2; exit 1; }
    # inputs, CPU references, the model and the reference H100 outputs
    aws s3 sync "${FTW_GOLDEN:?set FTW_GOLDEN}" "s3://$BUCKET/${PREFIX}golden/" --only-show-errors
    aws s3 ls "s3://$BUCKET/${PREFIX}golden/" --recursive --summarize | tail -n 2
    echo "AMI $(ami): $(aws ec2 describe-images --image-ids "$(ami)" --query 'Images[0].Name' --output text)" ;;
  bundle)
    ref=${2:-HEAD}; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    mkdir -p "$tmp/ftw/index"
    git archive "$ref" "$INF" pipeline/mosaics/keeplist.txt | tar x -C "$tmp/ftw"
    git rev-parse --short=12 "$ref" > "$tmp/ftw/CODE_VERSION"  # = prov_code of a checkout at REF
    # mosaics straight from the Source Coop bucket in us-west-2: the same objects as the
    # data.source.coop proxy, without the proxy hop or a transfer charge
    python3 - "${FTW_INDEX_DIR:?set FTW_INDEX_DIR}" "$tmp/ftw/index" <<'PY'
import pathlib, sys
import pyarrow.compute as pc, pyarrow.parquet as pq
src, dst = map(pathlib.Path, sys.argv[1:])
for p in sorted(src.glob("tile_index_*.parquet")):
    t = pq.read_table(p)
    for c in [c for c in t.column_names if c.endswith("_href")]:
        new = pc.replace_substring(t[c], "https://data.source.coop/",
                                   "https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/")
        t = t.set_column(t.column_names.index(c), c, new)
    pq.write_table(t, dst / p.name)
PY
    tar czf "$tmp/bundle.tar.gz" -C "$tmp" ftw
    aws s3 cp "$tmp/bundle.tar.gz" "s3://$BUCKET/${PREFIX}bundle.tar.gz" --only-show-errors
    echo "bundle $(du -h "$tmp/bundle.tar.gz" | cut -f1), code $(cat "$tmp/ftw/CODE_VERSION") ($ref)" ;;
  units)
    shift; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    python3 "$INF/aws/work_queue.py" units --years "$@" --nshards "${NSHARDS:-256}" \
      --index-dir "${FTW_INDEX_DIR:?set FTW_INDEX_DIR}" --out "$tmp"
    aws s3 sync "$tmp" "s3://$BUCKET/${PREFIX}units/" --only-show-errors
    echo "$(find "$tmp" -name '*.txt' | wc -l) units -> s3://$BUCKET/${PREFIX}units/" ;;
  up)
    n=${2:?count}; ud=$(mktemp); trap 'rm -f "$ud" "$ud.err"' EXIT
    userdata "$PREFIX" "$TYPE" "${MAX_UNITS:-0}" > "$ud"
    for ((i = 0; i < n; i++)); do run_one "$TYPE" "$ud" || { echo "no $TYPE capacity" >&2; exit 1; }; done ;;
  fleet)
    gpus=${2:?target GPUs}; ud=$(mktemp); trap 'rm -f "$ud"' EXIT
    userdata "$PREFIX" "$TYPE" "${MAX_UNITS:-0}" > "$ud"
    lt=$(printf '{"ImageId":"%s","IamInstanceProfile":{"Name":"%s"},"UserData":"%s","InstanceInitiatedShutdownBehavior":"terminate","MetadataOptions":{"HttpTokens":"required"},"BlockDeviceMappings":[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":300,"VolumeType":"gp3","Iops":6000,"Throughput":500,"DeleteOnTermination":true}}],"TagSpecifications":[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"%s"},{"Key":"project","Value":"ftw"},{"Key":"queue","Value":"%s"}]},{"ResourceType":"volume","Tags":[{"Key":"project","Value":"ftw"}]}]}' \
      "$(ami)" "$ROLE" "$(base64 -w0 "$ud")" "$NAME" "$PREFIX")
    if aws ec2 describe-launch-templates --launch-template-names "$NAME" > /dev/null 2>&1; then
      v=$(aws ec2 create-launch-template-version --launch-template-name "$NAME" --launch-template-data "$lt" --query LaunchTemplateVersion.VersionNumber --output text)
    else
      v=$(aws ec2 create-launch-template --launch-template-name "$NAME" --launch-template-data "$lt" --query LaunchTemplate.LatestVersionNumber --output text)
    fi
    w=$(gpu_spec "$TYPE" | awk '{print $2}')
    ov=$(for sn in "${SUBNET[@]}"; do printf '{"InstanceType":"%s","SubnetId":"%s","WeightedCapacity":%s},' "$TYPE" "$sn" "$w"; done)
    cfg=$(printf '[{"LaunchTemplateSpecification":{"LaunchTemplateName":"%s","Version":"%s"},"Overrides":[%s]}]' "$NAME" "$v" "${ov%,}")
    aws ec2 create-fleet "${DR[@]}" --type instant --launch-template-configs "$cfg" \
      --target-capacity-specification "TotalTargetCapacity=$gpus,DefaultTargetCapacityType=$([[ ${ONDEMAND:-0} == 1 ]] && echo on-demand || echo spot),TargetCapacityUnitType=units" \
      --spot-options AllocationStrategy=price-capacity-optimized --on-demand-options AllocationStrategy=lowest-price \
      --query '[Instances[].[InstanceType,InstanceIds[0]],Errors[].[ErrorCode,ErrorMessage]]' --output text ;;
  status)
    python3 "$INF/aws/work_queue.py" status --bucket "$BUCKET" --prefix "$PREFIX"
    aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" Name=instance-state-name,Values=pending,running \
      --query 'Reservations[].Instances[].[InstanceId,InstanceType,InstanceLifecycle,Placement.AvailabilityZone,LaunchTime]' --output text ;;
  down)
    read -r -a ids <<< "$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" Name=instance-state-name,Values=pending,running \
      --query 'Reservations[].Instances[].InstanceId' --output text)"
    (( ${#ids[@]} )) && aws ec2 terminate-instances "${DR[@]}" --instance-ids "${ids[@]}" --query 'TerminatingInstances[].[InstanceId,CurrentState.Name]' --output text ;;
  quotas)
    aws service-quotas list-service-quotas --service-code ec2 \
      --query "Quotas[?contains(QuotaName,' P ') || contains(QuotaName,'G and VT')].[QuotaCode,QuotaName,Value]" --output text ;;
esac
