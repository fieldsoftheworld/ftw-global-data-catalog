#!/bin/bash
# Outlines on EC2 CPU boxes (bootstrap.sh runs on each) and one year's chain after the outlines on
# one box (downstream.sh). From the repo root:
#   launch.sh setup              check the bucket, instance profile and subnets; upload the land polygons
#   launch.sh bundle [REF]       code of git REF (default HEAD) + indexes -> PREFIX/bundle.tar.gz
#   launch.sh units YEAR...      queue units {year}_{shard} (NSHARDS, default 256)
#   launch.sh up N               N outline boxes of TYPE, one run-instances each, over the subnets
#   launch.sh fleet VCPUS        an EC2 Fleet of ~VCPUS vCPUs over FLEET_TYPES (price-capacity-optimized)
#   launch.sh requeue            new units for the tiles of finished units that have no outline
#   launch.sh downstream YEAR    one on-demand DS_TYPE box: simplify, merge, fiboa, QA -> PREFIX/fiboa/YEAR/
#   launch.sh pull YEAR DIR      a finished year's fiboa files, merge summary and QA report -> DIR
#   launch.sh status | down      queue, downstream markers and boxes | terminate every box of PREFIX
# Required env: FTW_AWS_BUCKET (us-west-2), FTW_AWS_ROLE (instance profile with read/write on the
# bucket), FTW_AWS_SUBNETS ("us-west-2a=subnet-... us-west-2b=subnet-..."), FTW_INDEX_DIR
# (tile_index_{year}.parquet, pipeline/mosaics/indexes.py) for bundle and units,
# FTW_LAND_POLYGONS (sea_filter.py output) for setup.
# Optional: AWS_PROFILE, REGION (us-west-2), PREFIX (pp/), SCORES (score COG root, default
# s3://$FTW_AWS_BUCKET/infer/raster, the inference fleet's output), SCORES_NO_SIGN=1 (SCORES is a
# public bucket, e.g. s3://us-west-2.opendata.source.coop/ftw/global-data-2e/raster), TYPE
# (r7a.16xlarge), FLEET_TYPES, DS_TYPE (r6i.32xlarge), YEARS (claim only these), GB_PER_WORKER
# (10), MAX_HOURS (12 outlines, 10 downstream), OUTLINE_ARGS, MERGE_ARGS, ONDEMAND=1, DRYRUN=1.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export AWS_DEFAULT_REGION=${REGION:-us-west-2}
BUCKET=${FTW_AWS_BUCKET:?set FTW_AWS_BUCKET}
ROLE=${FTW_AWS_ROLE:?set FTW_AWS_ROLE}
PREFIX=${PREFIX:-pp/}
[[ $PREFIX == */ ]] || PREFIX=$PREFIX/
TYPE=${TYPE:-r7a.16xlarge}
# 8 GiB per vCPU: outlines are bound by memory (GB_PER_WORKER); the 24xlarge sizes were slow per vCPU
FLEET_TYPES=${FLEET_TYPES:-r7a.16xlarge r7a.32xlarge r7a.48xlarge r6a.16xlarge r6a.32xlarge r6i.16xlarge r6i.32xlarge r7i.16xlarge}
DS_TYPE=${DS_TYPE:-r6i.32xlarge}
WQ=pipeline/inference/aws/work_queue.py
HERE=pipeline/postprocessing/aws
AMI_PARAM=/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
declare -A SUBNET=()
for kv in ${FTW_AWS_SUBNETS:-}; do SUBNET[${kv%%=*}]=${kv#*=}; done
DR=(); [[ -n ${DRYRUN:-} ]] && DR=(--dry-run)

userdata() {  # template, year, max hours
  sed -e "s|__BUCKET__|$BUCKET|g" -e "s|__PREFIX__|$PREFIX|g" -e "s|__YEAR__|${2:-}|g" \
    -e "s|__SCORES__|${SCORES:-s3://$BUCKET/infer/raster}|g" -e "s|__SCORES_NO_SIGN__|${SCORES_NO_SIGN:-0}|g" \
    -e "s|__YEARS__|${YEARS:-}|g" -e "s|__MAX_HOURS__|$3|g" -e "s|__GB_PER_WORKER__|${GB_PER_WORKER:-10}|g" \
    -e "s|__OUTLINE_ARGS__|${OUTLINE_ARGS:-}|g" -e "s|__MERGE_ARGS__|${MERGE_ARGS:-}|g" \
    -e "s|__REGION__|$AWS_DEFAULT_REGION|g" "$HERE/$1"
}

ami() { aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text; }

block() {  # root volume of $1 GB
  printf 'DeviceName=/dev/xvda,Ebs={VolumeSize=%s,VolumeType=gp3,Iops=16000,Throughput=1000,DeleteOnTermination=true}' "$1"
}

run_one() {  # one box: types $1 (tried in order), user data file $2, Name tag $3, volume GB $4, market $5
  local t az market=()
  [[ $5 == spot ]] && market=(--instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}')
  for t in $1; do
    for az in "${!SUBNET[@]}"; do
      if aws ec2 run-instances "${DR[@]}" --image-id "$(ami)" --instance-type "$t" --count 1 \
        --subnet-id "${SUBNET[$az]}" --iam-instance-profile Name="$ROLE" "${market[@]}" \
        --instance-initiated-shutdown-behavior terminate --metadata-options HttpTokens=required \
        --block-device-mappings "$(block "$4")" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$3},{Key=project,Value=ftw},{Key=queue,Value=$PREFIX}]" \
          "ResourceType=volume,Tags=[{Key=project,Value=ftw}]" \
        --user-data "file://$2" --query 'Instances[].[InstanceId,InstanceType,Placement.AvailabilityZone]' \
        --output text 2> "$2.err"; then
        return 0
      fi
      grep -q DryRunOperation "$2.err" && { echo "dry run ok: $t in $az"; return 0; }
      echo "$t $az: $(tr '\n' ' ' < "$2.err" | tail -c 300)" >&2
    done
  done
  return 1
}

market() { [[ ${ONDEMAND:-0} == 1 ]] && echo on-demand || echo spot; }

case "${1:?setup | bundle | units | up | fleet | requeue | downstream | pull | status | down}" in
  setup)
    aws s3api head-bucket --bucket "$BUCKET"
    aws iam get-instance-profile --instance-profile-name "$ROLE" --query InstanceProfile.Arn --output text
    (( ${#SUBNET[@]} )) || { echo "set FTW_AWS_SUBNETS" >&2; exit 1; }
    aws s3 cp "${FTW_LAND_POLYGONS:?set FTW_LAND_POLYGONS}" "s3://$BUCKET/${PREFIX}land_polygons.parquet" --only-show-errors
    echo "AMI $(ami): $(aws ec2 describe-images --image-ids "$(ami)" --query 'Images[0].Name' --output text)" ;;
  bundle)
    ref=${2:-HEAD}; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    mkdir -p "$tmp/ftw"
    git archive "$ref" pipeline/postprocessing "$WQ" | tar x -C "$tmp/ftw"
    git rev-parse --short=12 "$ref" > "$tmp/ftw/CODE_VERSION"
    # B04 for the nodata QA from the Source Coop bucket in us-west-2, read without credentials
    python3 "$HERE/qa_index.py" "${FTW_INDEX_DIR:?set FTW_INDEX_DIR}" "$tmp/ftw/index"
    tar czf "$tmp/bundle.tar.gz" -C "$tmp" ftw
    aws s3 cp "$tmp/bundle.tar.gz" "s3://$BUCKET/${PREFIX}bundle.tar.gz" --only-show-errors
    echo "bundle $(du -h "$tmp/bundle.tar.gz" | cut -f1), code $(cat "$tmp/ftw/CODE_VERSION") ($ref)" ;;
  units)
    shift; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    python3 "$WQ" units --years "$@" --nshards "${NSHARDS:-256}" \
      --index-dir "${FTW_INDEX_DIR:?set FTW_INDEX_DIR}" --out "$tmp"
    aws s3 sync "$tmp" "s3://$BUCKET/${PREFIX}units/" --only-show-errors
    echo "$(find "$tmp" -name '*.txt' | wc -l) units -> s3://$BUCKET/${PREFIX}units/" ;;
  up)
    n=${2:?count}; ud=$(mktemp); trap 'rm -f "$ud" "$ud.err"' EXIT
    userdata bootstrap.sh "" "${MAX_HOURS:-12}" > "$ud"
    for ((i = 0; i < n; i++)); do
      run_one "$TYPE" "$ud" ftw-outlines 500 "$(market)" || { echo "no $TYPE capacity" >&2; exit 1; }
    done ;;
  fleet)
    vcpus=${2:?target vCPUs}; ud=$(mktemp); trap 'rm -f "$ud"' EXIT
    userdata bootstrap.sh "" "${MAX_HOURS:-12}" > "$ud"
    lt=$(printf '{"ImageId":"%s","IamInstanceProfile":{"Name":"%s"},"UserData":"%s","InstanceInitiatedShutdownBehavior":"terminate","MetadataOptions":{"HttpTokens":"required"},"BlockDeviceMappings":[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":500,"VolumeType":"gp3","Iops":16000,"Throughput":1000,"DeleteOnTermination":true}}],"TagSpecifications":[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"ftw-outlines"},{"Key":"project","Value":"ftw"},{"Key":"queue","Value":"%s"}]},{"ResourceType":"volume","Tags":[{"Key":"project","Value":"ftw"}]}]}' \
      "$(ami)" "$ROLE" "$(base64 -w0 "$ud")" "$PREFIX")
    if aws ec2 describe-launch-templates --launch-template-names ftw-outlines > /dev/null 2>&1; then
      v=$(aws ec2 create-launch-template-version --launch-template-name ftw-outlines --launch-template-data "$lt" --query LaunchTemplateVersion.VersionNumber --output text)
    else
      v=$(aws ec2 create-launch-template --launch-template-name ftw-outlines --launch-template-data "$lt" --query LaunchTemplate.LatestVersionNumber --output text)
    fi
    # each type weighted by its vCPUs, so the target is in vCPUs
    ov=$(for t in $FLEET_TYPES; do
      w=$(aws ec2 describe-instance-types --instance-types "$t" --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text)
      for sn in "${SUBNET[@]}"; do printf '{"InstanceType":"%s","SubnetId":"%s","WeightedCapacity":%s},' "$t" "$sn" "$w"; done
    done)
    cfg=$(printf '[{"LaunchTemplateSpecification":{"LaunchTemplateName":"ftw-outlines","Version":"%s"},"Overrides":[%s]}]' "$v" "${ov%,}")
    aws ec2 create-fleet "${DR[@]}" --type instant --launch-template-configs "$cfg" \
      --target-capacity-specification "TotalTargetCapacity=$vcpus,DefaultTargetCapacityType=$(market),TargetCapacityUnitType=units" \
      --spot-options AllocationStrategy=price-capacity-optimized --on-demand-options AllocationStrategy=lowest-price \
      --query '[Instances[].[InstanceType,InstanceIds[0]],Errors[].[ErrorCode,ErrorMessage]]' --output text ;;
  requeue)
    python3 "$WQ" requeue --bucket "$BUCKET" --prefix "$PREFIX" --outputs outlines ;;
  downstream)
    y=${2:?year}; ud=$(mktemp); trap 'rm -f "$ud" "$ud.err"' EXIT
    # a finished year is never rerun by accident; a failed attempt's marker is kept beside it
    if rc=$(aws s3 cp "s3://$BUCKET/${PREFIX}downstream/$y.done" - 2> /dev/null); then
      [[ $rc == 0 ]] && { echo "${PREFIX}downstream/$y.done is 0 (finished)" >&2; exit 1; }
      aws s3 cp "s3://$BUCKET/${PREFIX}downstream/$y.done" \
        "s3://$BUCKET/${PREFIX}downstream/$y.done.failed-$(date -u +%Y%m%dT%H%M%SZ)" --only-show-errors
      echo "earlier downstream $y attempt exited $rc; relaunching"
    fi
    userdata downstream.sh "$y" "${MAX_HOURS:-10}" > "$ud"
    run_one "$DS_TYPE" "$ud" ftw-downstream 2000 on-demand || { echo "no $DS_TYPE capacity" >&2; exit 1; } ;;
  pull)
    y=${2:?year}; dst=${3:?directory}
    rc=$(aws s3 cp "s3://$BUCKET/${PREFIX}downstream/$y.done" - 2> /dev/null || echo missing)
    [[ $rc == 0 ]] || { echo "downstream $y has not finished cleanly (marker: $rc)" >&2; exit 1; }
    aws s3 sync "s3://$BUCKET/${PREFIX}fiboa/$y/" "$dst/fiboa/$y/" --only-show-errors
    aws s3 cp "s3://$BUCKET/${PREFIX}merged/$y/_summary.json" "$dst/merged/$y/_summary.json" --only-show-errors
    aws s3 cp "s3://$BUCKET/${PREFIX}qa/vector_$y.json" "$dst/qa/vector_$y.json" --only-show-errors
    echo "$y: $(find "$dst/fiboa/$y" -name 'utm*.parquet' | wc -l) zone files in $dst/fiboa/$y" ;;
  status)
    python3 "$WQ" status --bucket "$BUCKET" --prefix "$PREFIX" --outputs outlines
    aws s3 ls "s3://$BUCKET/${PREFIX}downstream/" || true
    # shellcheck disable=SC2016  # JMESPath backticks, not shell
    aws ec2 describe-instances --filters "Name=tag:queue,Values=$PREFIX" Name=instance-state-name,Values=pending,running \
      --query 'Reservations[].Instances[].[Tags[?Key==`Name`]|[0].Value,InstanceId,InstanceType,InstanceLifecycle,Placement.AvailabilityZone,LaunchTime]' --output text ;;
  down)
    read -r -a ids <<< "$(aws ec2 describe-instances --filters "Name=tag:queue,Values=$PREFIX" Name=tag:Name,Values=ftw-outlines,ftw-downstream \
      Name=instance-state-name,Values=pending,running --query 'Reservations[].Instances[].InstanceId' --output text)"
    (( ${#ids[@]} )) || { echo "no boxes of ${PREFIX} running"; exit 0; }
    aws ec2 terminate-instances "${DR[@]}" --instance-ids "${ids[@]}" --query 'TerminatingInstances[].[InstanceId,CurrentState.Name]' --output text ;;
esac
