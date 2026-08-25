#!/usr/bin/env bash
# Bootstrap an EC2 backfill instance after code is extracted to INSTALL_ROOT.
set -euo pipefail

: "${INSTALL_ROOT:=/opt/br-sec-scrapers}"
: "${DB_SECRET_ARN:?DB_SECRET_ARN is required}"
: "${DB_NAME:=securitizacao}"
: "${SSM_PREFIX:?SSM_PREFIX is required}"
: "${LOG_GROUP_NAME:?LOG_GROUP_NAME is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${BACKFILL_MAX_SECONDS:=86400}"
: "${USE_BROWSER_FALLBACK:=false}"

LOG_DIR="/var/log/backfill"
mkdir -p "${LOG_DIR}"

exec >>"${LOG_DIR}/bootstrap.log" 2>&1
echo "backfill_bootstrap_start $(date -Is)"

if [[ ! -f "${INSTALL_ROOT}/scripts/run_backfill_all.py" ]]; then
  echo "missing backfill code at ${INSTALL_ROOT}" >&2
  exit 1
fi

dnf install -y python3.12 python3.12-pip awscli amazon-cloudwatch-agent

cd "${INSTALL_ROOT}"
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -e .

cat >/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "${LOG_DIR}/*.log",
            "log_group_name": "${LOG_GROUP_NAME}",
            "log_stream_name": "{instance_id}/backfill",
            "timezone": "UTC"
          }
        ]
      }
    }
  }
}
EOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

export DB_SECRET_ARN DB_NAME DB_SSLMODE=require
export SSM_PREFIX USE_BROWSER_FALLBACK AUTO_CREATE_SCHEMA=false LOG_LEVEL=INFO
export RUN_ID="${RUN_ID:-$(uuidgen)}"

# Prefer explicit env; otherwise read the instance tag "backfill_sources".
if [[ -z "${BACKFILL_SOURCES:-}" ]]; then
  TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
  if [[ -n "${TOKEN}" ]]; then
    INSTANCE_ID=$(curl -sf -H "X-aws-ec2-metadata-token: ${TOKEN}" \
      http://169.254.169.254/latest/meta-data/instance-id || true)
    if [[ -n "${INSTANCE_ID}" ]]; then
      BACKFILL_SOURCES=$(aws ec2 describe-tags \
        --region "${AWS_REGION}" \
        --filters "Name=resource-id,Values=${INSTANCE_ID}" "Name=key,Values=backfill_sources" \
        --query 'Tags[0].Value' --output text 2>/dev/null || true)
      if [[ "${BACKFILL_SOURCES}" == "None" ]]; then
        BACKFILL_SOURCES=""
      fi
    fi
  fi
fi
export BACKFILL_SOURCES="${BACKFILL_SOURCES:-}"

BACKFILL_ARGS=()
if [[ -n "${BACKFILL_SOURCES}" ]]; then
  BACKFILL_ARGS+=(--sources "${BACKFILL_SOURCES}")
fi

set +e
timeout "${BACKFILL_MAX_SECONDS}s" python3.12 scripts/run_backfill_all.py "${BACKFILL_ARGS[@]}" \
  | tee "${LOG_DIR}/run.log"
run_status=$?
set -e

echo "backfill_run_exit_status=${run_status} $(date -Is)"

bash "${INSTALL_ROOT}/scripts/ec2_backfill_teardown.sh"
echo "backfill_bootstrap_done $(date -Is)"
