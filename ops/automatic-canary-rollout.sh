#!/usr/bin/env bash
set -Eeuo pipefail

API_BASE_URL="${API_BASE_URL:-https://numueg.app}"
HOLD_SECONDS="${CANARY_HOLD_SECONDS:-3600}"
POLL_SECONDS="${CANARY_POLL_SECONDS:-20}"
FAILURE_THRESHOLD="${CANARY_FAILURE_THRESHOLD:-3}"
SSH_KEY="${PROD_EC2_SSH_KEY_PATH:-$HOME/.ssh/ec2_key}"
IMAGE_REF="${NUMU_API_IMAGE:?NUMU_API_IMAGE is required}"
SSH_USER="${PROD_EC2_USER:?PROD_EC2_USER is required}"
SSH_HOST="${PROD_EC2_HOST:?PROD_EC2_HOST is required}"
GHCR_TOKEN="${GHCR_TOKEN:?GHCR_TOKEN is required}"
GHCR_USER="${GHCR_USER:?GHCR_USER is required}"
GATE_SCRIPT="${CANARY_GATE_SCRIPT:-scripts/canary_gate.py}"
ROLLBACK_NEEDED=true
COMPLETED=false

die() { echo "ERROR: $*" >&2; return 1; }

notify() {
  local message="$1" payload
  [[ -n "${SLACK_WEBHOOK_URL:-}" ]] || return 0
  payload="$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$message")"
  curl -fsS --max-time 10 -H 'Content-Type: application/json' \
    --data "$payload" "$SLACK_WEBHOOK_URL" >/dev/null \
    || echo "WARNING: Slack notification failed" >&2
}

registry_login() {
  printf '%s' "$GHCR_TOKEN" | ssh -o BatchMode=yes -i "$SSH_KEY" \
    "$SSH_USER@$SSH_HOST" docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
}

remote_deploy() {
  local percent="$1"
  ssh -o BatchMode=yes -i "$SSH_KEY" "$SSH_USER@$SSH_HOST" \
    bash -s -- "$IMAGE_REF" "$percent" <<'REMOTE'
set -Eeuo pipefail
cd /opt/numu-api
/opt/numu-api/deploy-blue-green.sh "$1" "$2" deploy
REMOTE
}

remote_rollback() {
  ssh -o BatchMode=yes -i "$SSH_KEY" "$SSH_USER@$SSH_HOST" \
    bash -s -- "$IMAGE_REF" <<'REMOTE'
set -Eeuo pipefail
cd /opt/numu-api
/opt/numu-api/deploy-blue-green.sh "$1" 0 rollback
REMOTE
}

align_workers() {
  ssh -o BatchMode=yes -i "$SSH_KEY" "$SSH_USER@$SSH_HOST" bash -s <<'REMOTE'
set -Eeuo pipefail
cd /opt/numu-api
active_image="$(sed -n 's/^ACTIVE_IMAGE_REF=//p' .api-blue-green-state)"
docker pull "$active_image" || true
docker tag "$active_image" ghcr.io/numu-io/numu-api:prod
docker compose up -d --force-recreate celery-worker celery-beat
REMOTE
}

probe() {
  local variant="$1" body headers
  body="$(mktemp)"
  headers="$(mktemp)"
  if ! curl -fsS --max-time 10 -D "$headers" -o "$body" \
      -H "X-NUMU-Variant: $variant" "$API_BASE_URL/api/v1/health"; then
    rm -f "$body" "$headers"
    return 1
  fi
  if ! grep -q '"success":true' "$body" \
      || ! grep -Fqi "x-numu-variant: ${variant}" "$headers"; then
    rm -f "$body" "$headers"
    return 1
  fi
  rm -f "$body" "$headers"
}

guard_for_one_hold() {
  local label="$1" deadline stable_failures=0 candidate_failures=0
  deadline=$((SECONDS + HOLD_SECONDS))
  echo "==> Monitoring $label for ${HOLD_SECONDS}s..."
  while (( SECONDS < deadline )); do
    if probe stable; then
      stable_failures=0
    else
      stable_failures=$((stable_failures + 1))
      echo "WARNING: stable health failure $stable_failures/$FAILURE_THRESHOLD"
    fi
    if probe candidate; then
      candidate_failures=0
    else
      candidate_failures=$((candidate_failures + 1))
      echo "WARNING: candidate health failure $candidate_failures/$FAILURE_THRESHOLD"
    fi
    (( stable_failures < FAILURE_THRESHOLD )) \
      || die "stable triggered the health alert threshold during $label"
    (( candidate_failures < FAILURE_THRESHOLD )) \
      || die "candidate triggered the health alert threshold during $label"
    sleep "$POLL_SECONDS"
  done
}

full_gate() {
  python3 "$GATE_SCRIPT" --base-url "$API_BASE_URL" \
    --samples 20 --max-latency-ratio 1.25 --smoke
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$ROLLBACK_NEEDED" == true && "$COMPLETED" != true ]]; then
    echo "==> Canary failed; routing all traffic back to stable..." >&2
    if remote_rollback; then
      notify ":rotating_light: NUMU API canary rolled back to stable. ${GITHUB_RUN_URL:-}"
    else
      notify ":rotating_light: NUMU API canary failed AND automatic rollback failed. Manual action required. ${GITHUB_RUN_URL:-}"
      echo "ERROR: automatic rollback failed; manual action is required" >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT

[[ "$HOLD_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "CANARY_HOLD_SECONDS must be positive"
[[ "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "CANARY_POLL_SECONDS must be positive"
[[ "$FAILURE_THRESHOLD" =~ ^[1-9][0-9]*$ ]] || die "failure threshold must be positive"
[[ -f "$SSH_KEY" ]] || die "SSH key not found: $SSH_KEY"
[[ -f "$GATE_SCRIPT" ]] || die "canary gate not found: $GATE_SCRIPT"

registry_login
notify ":test_tube: NUMU API canary started at 10%. Kuma: https://status.numueg.app ${GITHUB_RUN_URL:-}"
guard_for_one_hold "10% traffic"
full_gate

echo "==> Gate passed; promoting candidate to 50%..."
remote_deploy 50
notify ":large_yellow_circle: NUMU API candidate promoted to 50%; monitoring for one more hour. ${GITHUB_RUN_URL:-}"
guard_for_one_hold "50% traffic"
full_gate

echo "==> Final gate passed; promoting candidate to 100%..."
remote_deploy 100
align_workers
probe stable || die "promoted API failed its final health check"
ROLLBACK_NEEDED=false
COMPLETED=true
notify ":large_green_circle: NUMU API candidate automatically promoted to 100%. ${GITHUB_RUN_URL:-}"
echo "==> Automatic canary rollout completed at 100%."
