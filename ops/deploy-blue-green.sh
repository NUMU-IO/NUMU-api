#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${NUMU_DEPLOY_ROOT:-/opt/numu-api}"
ENV_FILE="${NUMU_ENV_FILE:-$ROOT/.env}"
STATE_FILE="$ROOT/.api-blue-green-state"
ROUTER_DIR="$ROOT/api-router"
ROUTER_NAME="numu-api-router"
EDGE_NAME="numu-edge-nginx"
EDGE_CONFIG="$ROOT/edge-nginx.conf"
LOCAL_NAME="numu-api-prod"
LOCAL_NETWORK="numu-api_numu"
REMOTE_HOST="${NUMU_REMOTE_HOST:-108.133.34.24}"
REMOTE_USER="${NUMU_REMOTE_USER:-ec2-user}"
REMOTE_KEY="${NUMU_REMOTE_KEY:-/home/ec2-user/.ssh/numu-bg-tunnel}"
REMOTE_ROOT="${NUMU_REMOTE_ROOT:-/opt/numu-api-slot}"
REMOTE_NAME="numu-api-remote"
REMOTE_TUNNEL_BACKEND="172.18.0.1:18000"

die() { echo "ERROR: $*" >&2; exit 1; }

validate_percent() {
  if [[ ! "$1" =~ ^[0-9]+$ ]] || (( 10#$1 > 100 )); then
    die "traffic percent must be an integer from 0 to 100"
  fi
}

validate_location() {
  [[ "$1" == "local" || "$1" == "remote" ]] \
    || die "invalid deployment location: $1"
}

backend_for() {
  if [[ "$1" == "local" ]]; then
    echo "$LOCAL_NAME:8000"
  else
    echo "$REMOTE_TUNNEL_BACKEND"
  fi
}

render_router_config() {
  local active="$1" candidate="$2" percent="$3" output="$4" bucket
  local active_backend candidate_backend
  validate_location "$active"
  validate_location "$candidate"
  validate_percent "$percent"
  active_backend="$(backend_for "$active")"
  candidate_backend="$(backend_for "$candidate")"

  if (( 10#$percent == 0 )); then
    bucket="map \$http_x_numu_ab_key \$numu_bucket { default stable; }"
  elif (( 10#$percent == 100 )); then
    bucket="map \$http_x_numu_ab_key \$numu_bucket { default candidate; }"
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
        stable    ${active_backend};
        candidate ${candidate_backend};
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
            proxy_pass http://\$api_backend;
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
  render_router_config local remote 25 "$tmp/nginx.conf"
  grep -q '25% candidate' "$tmp/nginx.conf"
  grep -q "stable    $LOCAL_NAME:8000" "$tmp/nginx.conf"
  grep -q "candidate $REMOTE_TUNNEL_BACKEND" "$tmp/nginx.conf"
  render_router_config remote local 100 "$tmp/nginx.conf"
  grep -q 'default candidate' "$tmp/nginx.conf"
  grep -q "stable    $REMOTE_TUNNEL_BACKEND" "$tmp/nginx.conf"
  if docker info >/dev/null 2>&1; then
    docker run --rm -v "$tmp/nginx.conf:/etc/nginx/nginx.conf:ro" \
      nginx:1.27-alpine nginx -t >/dev/null
  fi
  echo "two-host blue/green router self-test passed"
}

if [[ "${1:-}" == "--self-test" ]]; then
  self_test
  exit 0
fi

IMAGE_REF="${1:-}"
TRAFFIC_PERCENT="${2:-100}"
ACTION="${3:-deploy}"
validate_percent "$TRAFFIC_PERCENT"
[[ "$ACTION" == "deploy" || "$ACTION" == "rollback" ]] \
  || die "action must be deploy or rollback"
if [[ "$ACTION" == "deploy" ]]; then
  [[ "$IMAGE_REF" =~ ^[a-zA-Z0-9._/@:-]+$ ]] \
    || die "usage: $0 IMAGE_REF [CANDIDATE_PERCENT] [deploy|rollback]"
fi
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found"
[[ -f "$REMOTE_KEY" ]] || die "$REMOTE_KEY not found"
docker network inspect "$LOCAL_NETWORK" >/dev/null 2>&1 \
  || die "Docker network $LOCAL_NETWORK not found"
systemctl is-active --quiet numu-bg-tunnel.service \
  || die "numu-bg-tunnel.service is not active"

REMOTE_SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$REMOTE_KEY" "$REMOTE_USER@$REMOTE_HOST")
REMOTE_SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=15 -i "$REMOTE_KEY")
remote() { "${REMOTE_SSH[@]}" "$@"; }

remote true || die "cannot reach remote API slot host"

read_state_value() {
  sed -n "s/^$1=//p" "$STATE_FILE" | tail -n1
}

slot_exists() {
  if [[ "$1" == "local" ]]; then
    docker inspect "$LOCAL_NAME" >/dev/null 2>&1
  else
    remote docker inspect "$REMOTE_NAME" >/dev/null 2>&1
  fi
}

slot_image_id() {
  if [[ "$1" == "local" ]]; then
    docker inspect "$LOCAL_NAME" --format '{{.Image}}'
  else
    remote docker inspect "$REMOTE_NAME" --format '{{.Image}}'
  fi
}

slot_image_ref() {
  local location="$1" id digest
  id="$(slot_image_id "$location")"
  if [[ "$location" == "local" ]]; then
    digest="$(docker image inspect "$id" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
    [[ -n "$digest" ]] || digest="$(docker inspect "$LOCAL_NAME" --format '{{.Config.Image}}')"
  else
    digest="$(remote docker image inspect "$id" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
    [[ -n "$digest" ]] || digest="$(remote docker inspect "$REMOTE_NAME" --format '{{.Config.Image}}')"
  fi
  echo "$digest"
}

wait_local() {
  for _ in $(seq 1 60); do
    if docker exec "$LOCAL_NAME" curl -fsS http://127.0.0.1:8000/api/v1/health \
      | grep -q '"success":true'; then
      return 0
    fi
    sleep 2
  done
  docker logs --tail=100 "$LOCAL_NAME" || true
  die "$LOCAL_NAME did not become healthy"
}

wait_remote() {
  for _ in $(seq 1 60); do
    if remote curl -fsS http://127.0.0.1:8000/api/v1/health \
      | grep -q '"success":true'; then
      return 0
    fi
    sleep 2
  done
  remote docker logs --tail=100 "$REMOTE_NAME" || true
  die "$REMOTE_NAME did not become healthy"
}

route_health() {
  docker exec "$ROUTER_NAME" wget -qO- \
    --header="X-NUMU-Variant: $1" http://127.0.0.1:8000/api/v1/health \
    | grep -q '"success":true'
}

remove_slot() {
  if [[ "$1" == "local" ]]; then
    docker rm -f "$LOCAL_NAME" >/dev/null 2>&1 || true
  else
    remote docker rm -f "$REMOTE_NAME" >/dev/null 2>&1 || true
  fi
}

run_local() {
  local image="$1"
  remove_slot local
  docker run -d \
    --name "$LOCAL_NAME" \
    --restart unless-stopped \
    --network "$LOCAL_NETWORK" \
    --env-file "$ENV_FILE" \
    -e ENVIRONMENT=staging -e DEBUG=false -e LOG_FORMAT=json \
    --log-driver awslogs \
    --log-opt awslogs-region=eu-west-1 \
    --log-opt awslogs-group=/numu/prod/api \
    --log-opt awslogs-create-group=true \
    --log-opt awslogs-stream=api-local \
    --log-opt mode=non-blocking --log-opt max-buffer-size=4m \
    --cpus 2 --memory 1200m \
    -p 8000:8000 \
    "$image" uvicorn src.main:app --host 0.0.0.0 --port 8000 \
      --workers 1 --no-access-log >/dev/null
  wait_local
}

run_remote() {
  local image="$1" available_kb
  remove_slot remote
  available_kb="$(remote "awk '/MemAvailable/{print \$2}' /proc/meminfo")"
  (( available_kb >= 550000 )) \
    || die "remote slot has less than 550 MiB available after cleanup"
  "${REMOTE_SCP[@]}" "$ENV_FILE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/.env.next"
  remote chmod 600 "$REMOTE_ROOT/.env.next"
  remote mv "$REMOTE_ROOT/.env.next" "$REMOTE_ROOT/.env"
  remote "timeout 3 bash -c '</dev/tcp/127.0.0.1/6380'" \
    || die "remote slot cannot reach production Redis tunnel"
  remote docker run -d \
    --name "$REMOTE_NAME" \
    --restart unless-stopped \
    --network host \
    --env-file "$REMOTE_ROOT/.env" \
    -e ENVIRONMENT=staging -e DEBUG=false -e LOG_FORMAT=json \
    -e REDIS_HOST=127.0.0.1 -e REDIS_PORT=6380 \
    --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
    --cpus 1.5 --memory 768m \
    "$image" uvicorn src.main:app --host 127.0.0.1 --port 8000 \
      --workers 1 --no-access-log >/dev/null
  wait_remote
}

apply_router() {
  local active="$1" candidate="$2" percent="$3"
  render_router_config "$active" "$candidate" "$percent" "$ROUTER_DIR/nginx.conf.next"
  cp "$ROUTER_DIR/nginx.conf" "$ROUTER_DIR/nginx.conf.previous"
  mv "$ROUTER_DIR/nginx.conf.next" "$ROUTER_DIR/nginx.conf"
  if ! docker exec "$ROUTER_NAME" nginx -t; then
    cp "$ROUTER_DIR/nginx.conf.previous" "$ROUTER_DIR/nginx.conf"
    die "router config validation failed; live routing was not changed"
  fi
  docker exec "$ROUTER_NAME" nginx -s reload
}

ensure_router() {
  local active="$1" edge_backup edge_next
  if ! docker inspect "$ROUTER_NAME" >/dev/null 2>&1; then
    mkdir -p "$ROUTER_DIR"
    render_router_config "$active" "$active" 0 "$ROUTER_DIR/nginx.conf"
    docker pull nginx:1.27-alpine
    docker run -d --name "$ROUTER_NAME" --restart unless-stopped \
      --network "$LOCAL_NETWORK" -v "$ROUTER_DIR:/etc/nginx:ro" \
      nginx:1.27-alpine >/dev/null
    for _ in $(seq 1 20); do
      route_health stable && break
      sleep 1
    done
    route_health stable || die "router bootstrap health check failed"
  fi

  if ! grep -Fq "map \$host \$api_up { default \"numu-api-router:8000\"; }" "$EDGE_CONFIG"; then
    grep -Fq "map \$host \$api_up { default \"172.18.0.1:8000\"; }" "$EDGE_CONFIG" \
      || die "cannot find the active API route in $EDGE_CONFIG"
    edge_backup="$EDGE_CONFIG.pre-blue-green.$(date +%Y%m%d%H%M%S)"
    edge_next="$EDGE_CONFIG.next"
    cp "$EDGE_CONFIG" "$edge_backup"
    sed "s|map \$host \$api_up { default \"172.18.0.1:8000\"; }|map \$host \$api_up { default \"numu-api-router:8000\"; }|" \
      "$EDGE_CONFIG" > "$edge_next"
    cat "$edge_next" > "$EDGE_CONFIG"
    rm -f "$edge_next"
    if ! docker exec "$EDGE_NAME" nginx -t; then
      cat "$edge_backup" > "$EDGE_CONFIG"
      docker exec "$EDGE_NAME" nginx -s reload || true
      die "edge Nginx rejected the router upstream; previous config restored"
    fi
    docker exec "$EDGE_NAME" nginx -s reload
  fi
}

write_state() {
  local active_id active_ref candidate_id=""
  active_id="$(slot_image_id "$ACTIVE_LOCATION")"
  active_ref="$(slot_image_ref "$ACTIVE_LOCATION")"
  if [[ -n "$CANDIDATE_LOCATION" ]]; then
    candidate_id="$(slot_image_id "$CANDIDATE_LOCATION")"
  fi
  cat > "$STATE_FILE.next" <<EOF
ACTIVE_LOCATION=$ACTIVE_LOCATION
ACTIVE_IMAGE_ID=$active_id
ACTIVE_IMAGE_REF=$active_ref
CANDIDATE_LOCATION=$CANDIDATE_LOCATION
CANDIDATE_IMAGE_ID=$candidate_id
PREVIOUS_LOCATION=$PREVIOUS_LOCATION
TRAFFIC_PERCENT=$TRAFFIC_PERCENT
EOF
  mv "$STATE_FILE.next" "$STATE_FILE"
}

if [[ -f "$STATE_FILE" ]]; then
  ACTIVE_LOCATION="$(read_state_value ACTIVE_LOCATION)"
  CANDIDATE_LOCATION="$(read_state_value CANDIDATE_LOCATION)"
  CANDIDATE_IMAGE_ID="$(read_state_value CANDIDATE_IMAGE_ID)"
  PREVIOUS_LOCATION="$(read_state_value PREVIOUS_LOCATION)"
  validate_location "$ACTIVE_LOCATION"
  slot_exists "$ACTIVE_LOCATION" || die "active $ACTIVE_LOCATION slot is missing"
else
  docker inspect "$LOCAL_NAME" >/dev/null 2>&1 \
    || die "initial local API container $LOCAL_NAME is missing"
  ACTIVE_LOCATION=local
  CANDIDATE_LOCATION=""
  CANDIDATE_IMAGE_ID=""
  PREVIOUS_LOCATION=""
fi

ensure_router "$ACTIVE_LOCATION"

if [[ "$ACTION" == "rollback" ]]; then
  if [[ -n "$CANDIDATE_LOCATION" ]]; then
    apply_router "$ACTIVE_LOCATION" "$CANDIDATE_LOCATION" 0
    route_health stable || die "stable route failed during rollback"
    remove_slot "$CANDIDATE_LOCATION"
    CANDIDATE_LOCATION=""
    PREVIOUS_LOCATION=""
  else
    [[ -n "$PREVIOUS_LOCATION" ]] || die "no retained slot is available for rollback"
    slot_exists "$PREVIOUS_LOCATION" || die "retained $PREVIOUS_LOCATION slot is missing"
    OLD_ACTIVE="$ACTIVE_LOCATION"
    apply_router "$PREVIOUS_LOCATION" "$OLD_ACTIVE" 0
    route_health stable || die "retained slot failed rollback health verification"
    ACTIVE_LOCATION="$PREVIOUS_LOCATION"
    PREVIOUS_LOCATION="$OLD_ACTIVE"
  fi
  apply_router "$ACTIVE_LOCATION" "${PREVIOUS_LOCATION:-$ACTIVE_LOCATION}" 0
  CANDIDATE_LOCATION=""
  TRAFFIC_PERCENT=0
  write_state
  echo "==> Rolled back all traffic to the $ACTIVE_LOCATION slot."
  exit 0
fi

docker pull "$IMAGE_REF" \
  || docker image inspect "$IMAGE_REF" >/dev/null 2>&1 \
  || die "candidate image is neither pullable nor cached locally"
IMAGE_ID="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}')"
IMAGE_DIGEST="$(docker image inspect "$IMAGE_REF" --format '{{index .RepoDigests 0}}')"

if [[ -n "$CANDIDATE_LOCATION" ]]; then
  [[ "$CANDIDATE_IMAGE_ID" == "$IMAGE_ID" ]] \
    || die "another candidate is under test; finish it at 0% or 100% first"
  TARGET_LOCATION="$CANDIDATE_LOCATION"
  echo "==> Reusing $TARGET_LOCATION candidate at $TRAFFIC_PERCENT% traffic..."
else
  if [[ "$ACTIVE_LOCATION" == "local" ]]; then
    TARGET_LOCATION=remote
  else
    TARGET_LOCATION=local
  fi

  echo "==> Applying backward-compatible migrations while $ACTIVE_LOCATION stays live..."
  docker run --rm --network "$LOCAL_NETWORK" --env-file "$ENV_FILE" \
    -e ENVIRONMENT=staging -e DEBUG=false -e LOG_FORMAT=json \
    "$IMAGE_DIGEST" alembic upgrade heads

  echo "==> Deploying and health-checking the $TARGET_LOCATION candidate..."
  if [[ "$TARGET_LOCATION" == "local" ]]; then
    run_local "$IMAGE_DIGEST"
  else
    remote docker pull "$IMAGE_REF" \
      || remote docker image inspect "$IMAGE_REF" >/dev/null 2>&1 \
      || die "candidate image is neither pullable nor cached remotely"
    run_remote "$IMAGE_REF"
  fi
  [[ "$(slot_image_id "$TARGET_LOCATION")" == "$IMAGE_ID" ]] \
    || die "candidate image does not match the requested build"
fi

apply_router "$ACTIVE_LOCATION" "$TARGET_LOCATION" "$TRAFFIC_PERCENT"
route_health stable || die "stable route failed health verification"
route_health candidate || {
  apply_router "$ACTIVE_LOCATION" "$ACTIVE_LOCATION" 0
  die "candidate route failed health verification; stable routing restored"
}

if (( 10#$TRAFFIC_PERCENT == 100 )); then
  OLD_ACTIVE="$ACTIVE_LOCATION"
  ACTIVE_LOCATION="$TARGET_LOCATION"
  PREVIOUS_LOCATION="$OLD_ACTIVE"
  CANDIDATE_LOCATION=""
  apply_router "$ACTIVE_LOCATION" "$PREVIOUS_LOCATION" 0
  route_health stable || die "promoted route failed final health verification"
elif (( 10#$TRAFFIC_PERCENT == 0 )); then
  remove_slot "$TARGET_LOCATION"
  CANDIDATE_LOCATION=""
  PREVIOUS_LOCATION=""
  apply_router "$ACTIVE_LOCATION" "$ACTIVE_LOCATION" 0
else
  CANDIDATE_LOCATION="$TARGET_LOCATION"
  PREVIOUS_LOCATION=""
fi

write_state
echo "==> Active: $ACTIVE_LOCATION; candidate: ${CANDIDATE_LOCATION:-none}; candidate traffic: $TRAFFIC_PERCENT%."
echo "==> X-NUMU-Variant selects stable or candidate; every response exposes the chosen variant."
