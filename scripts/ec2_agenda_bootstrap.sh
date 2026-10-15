#!/usr/bin/env bash
# Bootstrap an EC2 ANBIMA agenda instance after code is extracted to INSTALL_ROOT.
set -euo pipefail

: "${INSTALL_ROOT:=/opt/br-sec-scrapers}"
: "${DB_SECRET_ARN:?DB_SECRET_ARN is required}"
: "${DB_NAME:=securitizacao}"
: "${LOG_GROUP_NAME:?LOG_GROUP_NAME is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${AGENDA_MAX_SECONDS:=86400}"
: "${AGENDA_INPUT:=scripts/data/anbima_agenda_sample.csv}"

LOG_DIR="/var/log/agenda"
mkdir -p "${LOG_DIR}"

exec >>"${LOG_DIR}/bootstrap.log" 2>&1
echo "agenda_bootstrap_start $(date -Is)"

if [[ ! -f "${INSTALL_ROOT}/scripts/run_anbima_agenda.py" ]]; then
  echo "missing agenda code at ${INSTALL_ROOT}" >&2
  exit 1
fi

# Playwright Chromium system deps + Python.
dnf install -y \
  python3.12 python3.12-pip awscli amazon-cloudwatch-agent \
  atk at-spi2-atk cups-libs gtk3 libXcomposite libXcursor libXdamage \
  libXext libXi libXrandr libXScrnSaver libXtst pango alsa-lib nss nspr \
  xorg-x11-fonts-Type1 xorg-x11-fonts-misc

cd "${INSTALL_ROOT}"
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -e ".[browser]"
python3.12 -m playwright install chromium

cat >/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "${LOG_DIR}/*.log",
            "log_group_name": "${LOG_GROUP_NAME}",
            "log_stream_name": "{instance_id}/agenda",
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
export LOG_LEVEL=INFO
export EXECUTION_MODE="${EXECUTION_MODE:-ec2_agenda}"
export MAX_REQUESTS_PER_MINUTE="${MAX_REQUESTS_PER_MINUTE:-10}"
export REQUEST_DELAY_SECONDS="${REQUEST_DELAY_SECONDS:-6}"
export REQUEST_JITTER_SECONDS="${REQUEST_JITTER_SECONDS:-1}"
export RUN_ID="${RUN_ID:-$(uuidgen)}"

# Optional override from instance tag agenda_input.
if [[ -z "${AGENDA_INPUT_SET:-}" ]]; then
  TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
  if [[ -n "${TOKEN}" ]]; then
    INSTANCE_ID=$(curl -sf -H "X-aws-ec2-metadata-token: ${TOKEN}" \
      http://169.254.169.254/latest/meta-data/instance-id || true)
    if [[ -n "${INSTANCE_ID}" ]]; then
      TAGGED=$(aws ec2 describe-tags \
        --region "${AWS_REGION}" \
        --filters "Name=resource-id,Values=${INSTANCE_ID}" "Name=key,Values=agenda_input" \
        --query 'Tags[0].Value' --output text 2>/dev/null || true)
      if [[ -n "${TAGGED}" && "${TAGGED}" != "None" ]]; then
        AGENDA_INPUT="${TAGGED}"
      fi
    fi
  fi
fi

INPUT_PATH="${INSTALL_ROOT}/${AGENDA_INPUT}"
if [[ ! -f "${INPUT_PATH}" ]]; then
  echo "agenda input missing: ${INPUT_PATH}" >&2
  exit 1
fi

set +e
timeout "${AGENDA_MAX_SECONDS}s" python3.12 scripts/run_anbima_agenda.py \
  --input "${INPUT_PATH}" \
  --create-schema \
  | tee "${LOG_DIR}/run.log"
run_status=$?
set -e

echo "agenda_run_exit_status=${run_status} $(date -Is)"

bash "${INSTALL_ROOT}/scripts/ec2_agenda_teardown.sh"
echo "agenda_bootstrap_done $(date -Is)"
