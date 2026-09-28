#!/usr/bin/env bash
# AM5 — the test stack with Agent Market on it (compose projects `sran-test`
# and `sran-agent-market-test`), brought up the way AM4 did it by hand: in
# pieces, because this machine is short of memory beside the live stack.
# Nothing here names a container, volume or port of the live project.
#
#   bash scripts/probes/AM5/test_stack.sh up <tag>      # chat-edge + runner at ai-gateway-*:<tag>, flag on
#   bash scripts/probes/AM5/test_stack.sh edge <tag> [on|off]   # chat-edge alone (flag on by default)
#   bash scripts/probes/AM5/test_stack.sh runner <tag>  # the runner alone
#   bash scripts/probes/AM5/test_stack.sh runner-down   # the execution plane stopped
#   bash scripts/probes/AM5/test_stack.sh web           # web-chat dev server on :13002 (foreground)
#   bash scripts/probes/AM5/test_stack.sh status
#   bash scripts/probes/AM5/test_stack.sh down          # everything stopped, volumes kept
#
# The test runner's sandbox image is a tag of its own (ai-gateway-sandbox:am5test,
# made from AM5_SANDBOX_FROM, default :latest) so that a probe can take the tag
# away for one round — a real failed round — without touching the live runner's.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
rev() { docker inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true; }
export TS_BACKEND_TAG="${TS_BACKEND_TAG:-$(rev ai-gateway-backend-1)}"
export TS_GATEWAY_TAG="${TS_GATEWAY_TAG:-$(rev ai-gateway-gateway-1)}"
export TS_KB_MODELS_TAG="${TS_KB_MODELS_TAG:-$(rev ai-gateway-kb-models)}"
export NER_WORKERS=1
export AGENT_MARKET_ALLOWED_EMAILS=ts-owner@fix128.test AGENT_MARKET_ADMIN_EMAILS=ts-owner@fix128.test
export CORS_ORIGINS="https://localhost:3000,https://127.0.0.1:3000,https://localhost:3001,https://127.0.0.1:3001,https://localhost:3002,https://127.0.0.1:3002,http://localhost:5173,http://localhost:13002"
SANDBOX_TAG="${AM5_SANDBOX_TAG:-ai-gateway-sandbox:am5test}"
SANDBOX_FROM="${AM5_SANDBOX_FROM:-ai-gateway-sandbox:latest}"
ts() { docker compose -p sran-test -f docker-compose.test-stack.yml "$@" 2>&1 | grep -v "level=warning"; }
runner() {
  AM_NETWORK=sran-test_default AM_UPLOADS_VOLUME=sran-test_ts_chat_edge_uploads AM_CONTAINER_NAME=sran-test-agent-runner \
    AM_RUNNER_PORT=18095 AM_RUNNER_TAG="$1" AM_SANDBOX_HOST_DIR="$(pwd -W 2>/dev/null || pwd)/data/agent-sandbox-runs-test" \
    AGENT_MARKET_ENABLED=true SANDBOX_IMAGE="$SANDBOX_TAG" \
    AGENT_RUNNER_GATEWAY_API_KEY="$(tr -d '\r\n' < scripts/probes/AM1/results/runner_key.test)" \
    docker compose -p sran-agent-market-test -f docker-compose.agent-market.yml --profile agent-market "${@:2}" 2>&1 | grep -v "level=warning"
}
edge() { # <tag> <on|off>
  local flag=true
  [ "${2:-on}" = "off" ] && flag=false
  AGENT_MARKET_ENABLED=$flag TS_CHAT_EDGE_TAG="$1" ts up -d --no-deps --wait --wait-timeout 300 chat-edge-api | tail -2
  echo "chat-edge $(docker inspect -f '{{.Config.Image}} {{.State.Health.Status}}' sran-test-chat-edge-api-1) · flag $flag · availability $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:14000/api/v1/agent-market/availability)"
}

case "${1:-status}" in
  up)
    TAG="${2:?tag}"
    docker tag "$SANDBOX_FROM" "$SANDBOX_TAG"
    docker run --rm --privileged alpine:3.20 sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches; free -m | head -2'
    export TS_CHAT_EDGE_TAG="$TAG"
    ts up -d --no-deps --wait --wait-timeout 300 postgres redis postgres-webchat redis-webchat | tail -4
    ts up -d --no-deps --wait --wait-timeout 600 mock-provider ner-service rate-limiter log-worker dlp-sidecar backend gateway backup | tail -8
    runner "$TAG" up -d | tail -4 # the runner first: it migrates
    edge "$TAG" on
    docker logs sran-test-agent-runner --since 3m 2>&1 | grep -E "schema migrated|package registered|agent-runner started|already released|ERROR" | cut -c1-200 | tail -12
    ;;
  edge) edge "${2:?tag}" "${3:-on}" ;;
  runner)
    docker tag "$SANDBOX_FROM" "$SANDBOX_TAG"
    runner "${2:?tag}" up -d --force-recreate agent-runner | tail -3
    sleep 5
    docker logs sran-test-agent-runner --since 2m 2>&1 | grep -E "schema migrated|package registered|agent-runner started|already released|ERROR" | cut -c1-200 | tail -12
    ;;
  runner-down) runner latest down | tail -3 ;;
  web)
    cd apps/web-chat
    NEXT_PUBLIC_API_URL=http://localhost:14000/api/v1 NEXT_PUBLIC_GATEWAY_URL=http://localhost:18080 AGENT_MARKET_ENABLED=true \
      NEXT_PUBLIC_BUILD_ID="$(git rev-parse --short=7 HEAD)" npx next dev -p 13002
    ;;
  status)
    docker ps -a --filter label=com.docker.compose.project=sran-test --format '{{.Names}} | {{.Image}} | {{.Status}}'
    docker ps -a --filter label=com.docker.compose.project=sran-agent-market-test --format '{{.Names}} | {{.Image}} | {{.Status}}'
    ;;
  down)
    runner latest down | tail -2
    ts down --remove-orphans | tail -3
    docker rmi "$SANDBOX_TAG" 2>/dev/null | tail -1
    ;;
  *) echo "usage: $0 up <tag>|edge <tag> [on|off]|runner <tag>|runner-down|web|status|down" >&2; exit 2 ;;
esac
