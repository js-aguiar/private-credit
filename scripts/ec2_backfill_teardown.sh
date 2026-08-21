#!/usr/bin/env bash
# Terminate this backfill instance and delete any non-root EBS volumes.
set -euo pipefail

TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
meta() {
  curl -sf -H "X-aws-ec2-metadata-token: ${TOKEN}" "http://169.254.169.254/latest/meta-data/$1"
}

INSTANCE_ID=$(meta instance-id)
AZ=$(meta placement/availability-zone)
REGION="${AWS_REGION:-${AZ::-1}}"

echo "teardown_start instance_id=${INSTANCE_ID} region=${REGION} $(date -Is)"

ROOT_DEVICE=$(aws ec2 describe-instances \
  --region "${REGION}" \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].RootDeviceName' \
  --output text)

mapfile -t VOLUME_IDS < <(aws ec2 describe-instances \
  --region "${REGION}" \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[].Ebs.VolumeId' \
  --output text | tr '\t' '\n')

for volume_id in "${VOLUME_IDS[@]}"; do
  [[ -z "${volume_id}" || "${volume_id}" == "None" ]] && continue
  device=$(aws ec2 describe-instances \
    --region "${REGION}" \
    --instance-ids "${INSTANCE_ID}" \
    --query "Reservations[0].Instances[0].BlockDeviceMappings[?Ebs.VolumeId=='${volume_id}'].DeviceName" \
    --output text)
  if [[ "${device}" != "${ROOT_DEVICE}" ]]; then
    echo "detaching_non_root_volume volume=${volume_id} device=${device}"
    aws ec2 detach-volume --region "${REGION}" --volume-id "${volume_id}" || true
    aws ec2 wait volume-available --region "${REGION}" --volume-ids "${volume_id}" || true
    aws ec2 delete-volume --region "${REGION}" --volume-id "${volume_id}" || true
  fi
done

aws ec2 terminate-instances --region "${REGION}" --instance-ids "${INSTANCE_ID}"
echo "teardown_terminate_requested $(date -Is)"
