#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${NUMU_DEPLOY_ROOT:-/opt/numu-api}"
ENV_FILE="${NUMU_ENV_FILE:-$ROOT/.env.prod}"
STATE_FILE="$ROOT/.api-blue-green-state"
ROUTER_DIR="$ROOT/api-router"
ROUTER_NAME="numu-api-router"
LEGACY_NAME="numu-api-prod"

die() { echo "ERROR: $*" >&2; exit 1; }

validate_percent() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 <= 100 )) \
    || die "traffic percent must be an integer from 0 to 100"
}

render_router_config() {
  local active="$1" candidate="$2" percent="$3" output="$4" bucket
  validate_percent "$percent"
  [[ "$active" =~ ^numu-api-(blue|green)$ ]] || die "invalid active slot: $active"
  [[ "$candidate" =~ ^numu-api-(blue|green)$ ]] || die "invalid candidate slot: $candidate"

  if (( 10#$percent == 0 )); then
    bucket='map $http_x_numu_ab_key $numu_bucket { default stable; }'
  elif (( 10#$percent == 100 )); then
    bucket='map $http_x_numu_ab_key $numu_bucket { default candidate; }'
  else
    bucket="split_clients \"\${http_x_numu_ab_key}\${http_x_forwarded_for}\${remote_addr}\${http_user_agent}\" \$numu_bucket {
        ${percent}% candidate;
        * stable;
    }"
  fi

  mkdir -p "$(dirname "$output")"
  cat > "$output" <<EOF
events {}
http {
    resolver 127.0.0.11 valid=5s ipv6=off;
    ${bucket}
    map \$http_x_numu_variant \$numu_variant {
        default   \$numu_bucket;
        stable    stable;
        candidate candidate;
    }
    map \$numu_variant \$api_backend {
        stable    ${active};
        candidate ${candidate};
    }
    map \$http_x_real_ip \$numu_real_ip {
        default \$http_x_real_ip;
        ""      \$remote_addr;
    }
    map \$http_x_forwarded_proto \$numu_forwarded_proto {
        default \$http_x_forwarded_proto;
        ""      \$scheme;
    }
    server {
        listen 8000;
        client_max_body_size 10m;
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        location / {
            proxy_pass http://\$api_backend:8000;
            proxy_http_version 1.1;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$numu_real_ip;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$numu_forwarded_proto;
            proxy_set_header X-Request-ID \$request_id;
            proxy_set_header X-NUMU-Variant \$numu_variant;
            add_header X-NUMU-Variant \$numu_variant always;
        }
    }
}
EOF
}

self_test() {
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  render_router_config numu-api-blue numu-api-green 25 "$tmp/nginx.conf"
  grep -q '25% candidate' "$tmp/nginx.conf"
  grep -q 'stable    numu-api-blue' "$tmp/nginx.conf"
  grep -q 'candidate numu-api-green' "$tmp/nginx.conf"
  render_router_config numu-api-green numu-api-blue 100 "$tmp/nginx.conf"
  grep -q 'default candidate' "$tmp/nginx.conf"
  if docker info >/dev/null 2>&1; then
    docker run --rm -v "$tmp/nginx.conf:/etc/nginx/nginx.conf:ro" \
      nginx:1.27-alpine nginx -t >/dev/null
  fi
  echo "blue/green router self-test passed"
}

if [[ "${1:-}" == "--self-test" ]]; then
  self_test
  exit 0
fi

IMAGE_REF="${1:-}"
TRAFFIC_PERCENT="${2:-100}"
ACTION="${3:-deploy}"
[[ -n "$IMAGE_REF" ]] || die "usage: $0 IMAGE_REF [CANDIDATE_PERCENT]"
validate_percent "$TRAFFIC_PERCENT"
[[ "$ACTION" == "deploy" || "$ACTION" == "rollback" ]] || die "action must be deploy or rollback"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found"

wait_healthy() {
  local container="$1"
  for _ in $(seq 1 60); do
    if docker exec "$container" curl -fsS http://localhost:8000/api/v1/health \
      | grep -q '"success":true'; then
      return 0
    fi
    sleep 2
  done
  docker logs --tail=100 "$container" || true
  die "$container did not become healthy"
}

run_api() {
  local name="$1" image="$2"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d \
    --name "$name" \
    --restart unless-stopped \
    --network "$NETWORK" \
    --env-file "$ENV_FILE" \
    -e ENVIRONMENT=production \
    -e DEBUG=false \
    -e LOG_FORMAT=json \
    --log-driver awslogs \
    --log-opt awslogs-region=eu-west-1 \
    --log-opt awslogs-group=/numu/prod/api \
    --log-opt awslogs-create-group=true \
    --log-opt "awslogs-stream=$name" \
    --cpus 4 --memory 4g \
    "$image" >/dev/null
  wait_healthy "$name"
}

read_state_value() {
  sed -n "s/^$1=//p" "$STATE_FILE" | tail -n1
}

if [[ "$ACTION" == "rollback" ]]; then
  [[ -f "$STATE_FILE" ]] || die "blue/green state does not exist; nothing can be rolled back"
  ACTIVE_NAME="$(read_state_value ACTIVE_NAME)"
  CANDIDATE_NAME="$(read_state_value CANDIDATE_NAME)"
  NETWORK="$(read_state_value NETWORK)"
  [[ "$ACTIVE_NAME" =~ ^numu-api-(blue|green)$ ]] || die "invalid active slot in $STATE_FILE"

  if [[ -n "$CANDIDATE_NAME" ]]; then
    TARGET_NAME="$CANDIDATE_NAME"
    ROLLBACK_PERCENT=0
    HEALTH_VARIANT=stable
  else
    if [[ "$ACTIVE_NAME" == "numu-api-blue" ]]; then
      TARGET_NAME=numu-api-green
    else
      TARGET_NAME=numu-api-blue
    fi
    ROLLBACK_PERCENT=100
    HEALTH_VARIANT=candidate
  fi
  docker inspect "$TARGET_NAME" >/dev/null 2>&1 || die "retained rollback slot $TARGET_NAME is missing"
  docker inspect "$ROUTER_NAME" >/dev/null 2>&1 || die "$ROUTER_NAME is missing"

  render_router_config "$ACTIVE_NAME" "$TARGET_NAME" "$ROLLBACK_PERCENT" "$ROUTER_DIR/nginx.conf.next"
  cp "$ROUTER_DIR/nginx.conf" "$ROUTER_DIR/nginx.conf.previous"
  mv "$ROUTER_DIR/nginx.conf.next" "$ROUTER_DIR/nginx.conf"
  if ! docker exec "$ROUTER_NAME" nginx -t; then
    cp "$ROUTER_DIR/nginx.conf.previous" "$ROUTER_DIR/nginx.conf"
    die "rollback config validation failed; live routing was not changed"
  fi
  docker exec "$ROUTER_NAME" nginx -s reload
  curl -fsS -H "X-NUMU-Variant: $HEALTH_VARIANT" http://127.0.0.1:8000/api/v1/health \
    | grep -q '"success":true' || {
      cp "$ROUTER_DIR/nginx.conf.previous" "$ROUTER_DIR/nginx.conf"
      docker exec "$ROUTER_NAME" nginx -s reload || true
      die "rollback slot failed health verification; previous routing restored"
    }
  if (( ROLLBACK_PERCENT == 0 )); then
    docker rm -f "$TARGET_NAME" >/dev/null 2>&1 || true
  else
    ACTIVE_NAME="$TARGET_NAME"
  fi
  ACTIVE_IMAGE_ID="$(docker inspect "$ACTIVE_NAME" --format '{{.Image}}')"
  cat > "$STATE_FILE.next" <<EOF
ACTIVE_NAME=$ACTIVE_NAME
CANDIDATE_NAME=
NETWORK=$NETWORK
TRAFFIC_PERCENT=$ROLLBACK_PERCENT
ACTIVE_IMAGE_ID=$ACTIVE_IMAGE_ID
EOF
  mv "$STATE_FILE.next" "$STATE_FILE"
  echo "==> Rolled back all traffic to $ACTIVE_NAME."
  exit 0
fi

docker pull "$IMAGE_REF"
IMAGE_ID="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}')"

if [[ -f "$STATE_FILE" ]]; then
  ACTIVE_NAME="$(read_state_value ACTIVE_NAME)"
  CANDIDATE_NAME="$(read_state_value CANDIDATE_NAME)"
  [[ "$ACTIVE_NAME" =~ ^numu-api-(blue|green)$ ]] || die "invalid active slot in $STATE_FILE"
  NETWORK="$(read_state_value NETWORK)"
  docker inspect "$ACTIVE_NAME" >/dev/null 2>&1 || die "active slot $ACTIVE_NAME is missing"
else
  docker inspect "$LEGACY_NAME" >/dev/null 2>&1 || die "neither blue/green state nor $LEGACY_NAME exists"
  NETWORK="$(docker inspect "$LEGACY_NAME" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' | head -n1)"
  [[ -n "$NETWORK" ]] || die "could not determine the production Docker network"
  echo "==> Bootstrapping blue from the currently running production image..."
  LEGACY_IMAGE_ID="$(docker inspect "$LEGACY_NAME" --format '{{.Image}}')"
  run_api numu-api-blue "$LEGACY_IMAGE_ID"
  ACTIVE_NAME=numu-api-blue
  CANDIDATE_NAME=""
fi

if [[ -n "$CANDIDATE_NAME" ]]; then
  CANDIDATE_IMAGE_ID="$(docker inspect "$CANDIDATE_NAME" --format '{{.Image}}' 2>/dev/null || true)"
  [[ "$CANDIDATE_IMAGE_ID" == "$IMAGE_ID" ]] \
    || die "another candidate is under test; finish it at 0% or 100% before deploying a new image"
  TARGET_NAME="$CANDIDATE_NAME"
  echo "==> Reusing $TARGET_NAME and changing its traffic share to $TRAFFIC_PERCENT%..."
else
  if [[ "$ACTIVE_NAME" == "numu-api-blue" ]]; then
    TARGET_NAME=numu-api-green
  else
    TARGET_NAME=numu-api-blue
  fi

  echo "==> Applying backward-compatible migrations while $ACTIVE_NAME stays live..."
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" \
    -e ENVIRONMENT=production -e DEBUG=false -e LOG_FORMAT=json \
    "$IMAGE_ID" alembic upgrade heads

  echo "==> Starting and health-checking inactive slot $TARGET_NAME..."
  run_api "$TARGET_NAME" "$IMAGE_ID"
fi

mkdir -p "$ROUTER_DIR"
if (( 10#$TRAFFIC_PERCENT == 0 )); then
  ROUTER_CANDIDATE="$ACTIVE_NAME"
else
  ROUTER_CANDIDATE="$TARGET_NAME"
fi
render_router_config "$ACTIVE_NAME" "$ROUTER_CANDIDATE" "$TRAFFIC_PERCENT" "$ROUTER_DIR/nginx.conf.next"

if docker inspect "$ROUTER_NAME" >/dev/null 2>&1; then
  cp "$ROUTER_DIR/nginx.conf" "$ROUTER_DIR/nginx.conf.previous"
  mv "$ROUTER_DIR/nginx.conf.next" "$ROUTER_DIR/nginx.conf"
  if ! docker exec "$ROUTER_NAME" nginx -t; then
    mv "$ROUTER_DIR/nginx.conf.previous" "$ROUTER_DIR/nginx.conf"
    die "router config validation failed; live routing was not changed"
  fi
  docker exec "$ROUTER_NAME" nginx -s reload
else
  echo "==> One-time cutover: validating the router on port 18000..."
  mv "$ROUTER_DIR/nginx.conf.next" "$ROUTER_DIR/nginx.conf"
  docker pull nginx:1.27-alpine
  docker rm -f numu-api-router-check >/dev/null 2>&1 || true
  docker run -d --name numu-api-router-check --network "$NETWORK" \
    -p 18000:8000 -v "$ROUTER_DIR:/etc/nginx:ro" nginx:1.27-alpine >/dev/null
  for _ in $(seq 1 20); do
    curl -fsS http://127.0.0.1:18000/api/v1/health | grep -q '"success":true' && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:18000/api/v1/health | grep -q '"success":true' \
    || die "router validation failed; legacy API is still live"
  # Redirect new external connections to the validated temporary router while
  # port 8000 changes ownership. Existing requests drain in the legacy API.
  sudo -n iptables --version >/dev/null 2>&1 \
    || die "passwordless sudo for iptables is required for the zero-downtime bootstrap"
  REDIRECT_RULE=(-p tcp --dport 8000 -m comment --comment numu-api-router-cutover -j REDIRECT --to-ports 18000)
  sudo -n iptables -t nat -I PREROUTING 1 "${REDIRECT_RULE[@]}"
  docker stop "$LEGACY_NAME" >/dev/null
  if ! docker run -d --name "$ROUTER_NAME" --restart unless-stopped \
    --network "$NETWORK" -p 8000:8000 \
    -v "$ROUTER_DIR:/etc/nginx:ro" nginx:1.27-alpine >/dev/null; then
    docker rm -f "$ROUTER_NAME" >/dev/null 2>&1 || true
    docker start "$LEGACY_NAME" >/dev/null
    sudo -n iptables -t nat -D PREROUTING "${REDIRECT_RULE[@]}" || true
    docker rm -f numu-api-router-check >/dev/null 2>&1 || true
    die "router failed to claim port 8000; legacy API was restarted"
  fi
  curl -fsS http://127.0.0.1:8000/api/v1/health | grep -q '"success":true' || {
    docker rm -f "$ROUTER_NAME" >/dev/null 2>&1 || true
    docker start "$LEGACY_NAME" >/dev/null
    sudo -n iptables -t nat -D PREROUTING "${REDIRECT_RULE[@]}" || true
    docker rm -f numu-api-router-check >/dev/null 2>&1 || true
    die "router failed after binding port 8000; legacy API was restarted"
  }
  sudo -n iptables -t nat -D PREROUTING "${REDIRECT_RULE[@]}"
  docker rm -f numu-api-router-check >/dev/null
fi

for variant in stable candidate; do
  curl -fsS -H "X-NUMU-Variant: $variant" http://127.0.0.1:8000/api/v1/health \
    | grep -q '"success":true' || {
      if [[ -f "$ROUTER_DIR/nginx.conf.previous" ]]; then
        cp "$ROUTER_DIR/nginx.conf.previous" "$ROUTER_DIR/nginx.conf"
        docker exec "$ROUTER_NAME" nginx -s reload || true
      elif docker inspect "$LEGACY_NAME" >/dev/null 2>&1; then
        docker rm -f "$ROUTER_NAME" >/dev/null 2>&1 || true
        docker start "$LEGACY_NAME" >/dev/null || true
      fi
      die "$variant route failed health verification; previous routing restored"
    }
done

if (( 10#$TRAFFIC_PERCENT == 100 )); then
  ACTIVE_NAME="$TARGET_NAME"
  CANDIDATE_NAME=""
elif (( 10#$TRAFFIC_PERCENT == 0 )); then
  docker rm -f "$TARGET_NAME" >/dev/null 2>&1 || true
  CANDIDATE_NAME=""
else
  CANDIDATE_NAME="$TARGET_NAME"
fi

ACTIVE_IMAGE_ID="$(docker inspect "$ACTIVE_NAME" --format '{{.Image}}')"
cat > "$STATE_FILE.next" <<EOF
ACTIVE_NAME=$ACTIVE_NAME
CANDIDATE_NAME=$CANDIDATE_NAME
NETWORK=$NETWORK
TRAFFIC_PERCENT=$TRAFFIC_PERCENT
ACTIVE_IMAGE_ID=$ACTIVE_IMAGE_ID
EOF
mv "$STATE_FILE.next" "$STATE_FILE"

if docker inspect "$LEGACY_NAME" >/dev/null 2>&1; then
  docker rm -f "$LEGACY_NAME" >/dev/null
fi

echo "==> Active: $ACTIVE_NAME; candidate: ${CANDIDATE_NAME:-none}; candidate traffic: $TRAFFIC_PERCENT%."
echo "==> Override a request with X-NUMU-Variant: stable or candidate; responses expose the selected variant."
