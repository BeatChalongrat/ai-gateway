#!/usr/bin/env bash
# AM7 — the test stack with Agent Market on it (compose projects `sran-test`
# and `sran-agent-market-test`), brought up in pieces like AM6 (this machine
# is short of memory beside the live stack), with what AM7 adds:
#
#   * the backend at the AM7 revision too (its /internal/admin-audit writes
#     Agent Market's admin changes into admin_audit_log);
#   * the local SMTP sink (service mail-sink, Mailpit): the relay is set to
#     host mail-sink, port 1025, no encryption, on the admin page; what arrived
#     is at http://127.0.0.1:18025 (/api/v1/messages);
#   * admin-ui as a dev server on :13000 against the test backend (18081) and
#     the test chat-edge (14000) — the Agent Market page is there.
#
# Nothing here names a container, volume or port of the live project.
#
#   bash scripts/agent-market/test_stack.sh up <tag>        # backend + chat-edge + runner at <tag>, sink, flag on
#   bash scripts/agent-market/test_stack.sh check <tag>     # red unless every container runs, healthy, on the images asked
#   bash scripts/agent-market/test_stack.sh edge <tag> [on|off]
#   bash scripts/agent-market/test_stack.sh runner <tag>
#   bash scripts/agent-market/test_stack.sh seed            # the test stack's owner, models, and three admin-ui accounts
#   bash scripts/agent-market/test_stack.sh web             # web-chat dev server on :13002 (foreground)
#   bash scripts/agent-market/test_stack.sh admin           # admin-ui dev server on :13000 (foreground)
#   bash scripts/agent-market/test_stack.sh fresh           # agent_market dropped on the TEST chat DB (next up migrates 001–006)
#   bash scripts/agent-market/test_stack.sh status | down
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$ROOT/scripts/ops/rev.sh" # AM24b: the short revision, one rule (rev_tag / rev_image)
# (AM24b: a backend tag given by hand, as any revision, is the tag its image carries)
[ -z "${TS_BACKEND_TAG:-}" ] || TS_BACKEND_TAG="$(rev_existing_tag ai-gateway-backend "$TS_BACKEND_TAG")"
cd "$ROOT"
rev() { docker inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true; }
export TS_GATEWAY_TAG="${TS_GATEWAY_TAG:-$(rev ai-gateway-gateway-1)}"
export TS_KB_MODELS_TAG="${TS_KB_MODELS_TAG:-$(rev ai-gateway-kb-models)}"
export NER_WORKERS=1
export AGENT_MARKET_ALLOWED_EMAILS=ts-owner@fix128.test AGENT_MARKET_ADMIN_EMAILS=ts-owner@fix128.test
export CORS_ORIGINS="https://localhost:3000,https://127.0.0.1:3000,https://localhost:3001,https://127.0.0.1:3001,https://localhost:3002,https://127.0.0.1:3002,http://localhost:5173,http://localhost:13002,http://localhost:13000"
SANDBOX_TAG="${AM7_SANDBOX_TAG:-ai-gateway-sandbox:am7test}"
SANDBOX_FROM="${AM7_SANDBOX_FROM:-ai-gateway-sandbox:latest}"
dotenv() { grep "^$1=" .env | cut -d= -f2- | tr -d '\r\n'; }
ts() { docker compose -p sran-test -f docker-compose.test-stack.yml "$@" 2>&1 | grep -v "level=warning"; }
runner() {
  # AM12: the test stack's fetcher has no route to the real web. Its `web` network is replaced by an internal
  # one on a public address range (scripts/agent-market/test-web.yml), where stand-in sites can be put
  # (scripts/agent-market/test_stack_web.sh web); with nothing on it, every address is simply unreachable.
  docker network inspect sran-test-web >/dev/null 2>&1 ||
    docker network create --internal --subnet 93.184.215.0/24 --label sran.test-stack=true sran-test-web >/dev/null
  AM_FETCHER_TAG="${AM_FETCHER_TAG:-$1}" \
  AM_NETWORK=sran-test_default AM_UPLOADS_VOLUME=sran-test_ts_chat_edge_uploads AM_CONTAINER_NAME=sran-test-agent-runner \
    AM_RUNNER_PORT=18095 AM_RUNNER_TAG="$1" AM_SANDBOX_HOST_DIR="$(pwd -W 2>/dev/null || pwd)/data/agent-sandbox-runs-test" \
    AGENT_MARKET_ENABLED=true SANDBOX_IMAGE="$SANDBOX_TAG" AGENT_MARKET_SMTP_PASSWORD= WEB_CHAT_PUBLIC_URL=http://localhost:13002/chat \
    AGENT_RUNNER_GATEWAY_API_KEY="$(tr -d '\r\n' < scripts/probes/AM1/results/runner_key.test)" \
    docker compose -p sran-agent-market-test -f docker-compose.agent-market.yml -f scripts/agent-market/test-web.yml       --profile agent-market "${@:2}" 2>&1 | grep -v "level=warning"
}
need_images() { # AM19b: every image the stack would run is on this host, or nothing is started
  # (14 Oct 2026: an 8-character sha made Compose pull a tag that does not exist; the edge stayed down and
  # the step still said ok. A missing image is an error, named here, before anything is touched.)
  local missing=() img
  for img; do docker image inspect "$img" > /dev/null 2>&1 || missing+=("$img"); done
  [ "${#missing[@]}" = 0 ] && return 0
  echo "RED: not on this host: ${missing[*]} — the stack was not started (a tag is the 7-character sha of a commit whose images were built: gate.sh build <sha>)"
  exit 6
}
check() { # <tag> — AM19b: the stack is what was asked, or this is red naming what is not
  # Every container the stack started is running (a one-shot that exited 0 is done, not down), every
  # health check says healthy, and the edge, the runner and the backend run the images asked for.
  local tag="$1" red=0 names n line status code health image want running=0 healthy=0
  names=$( { docker ps -a --filter label=com.docker.compose.project=sran-test --format '{{.Names}}'
             docker ps -a --filter label=com.docker.compose.project=sran-agent-market-test --format '{{.Names}}'; } | sort -u )
  for n in sran-test-chat-edge-api-1 sran-test-agent-runner sran-test-backend-1; do
    case " $(echo $names) " in *" $n "*) ;; *) echo "RED: $n is not there"; red=1 ;; esac
  done
  for n in $names; do
    line=$(docker inspect -f '{{.Name}} {{.State.Status}} {{.State.ExitCode}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} {{.Config.Image}}' "$n" 2>/dev/null)
    read -r _ status code health image <<< "$line"
    want=
    case "$n" in
      sran-test-chat-edge-api-1) want="ai-gateway-chat-edge-api:$tag" ;;
      sran-test-agent-runner) want="ai-gateway-agent-runner:$tag" ;;
      sran-test-backend-1) want="ai-gateway-backend:${TS_BACKEND_TAG:-$tag}" ;;
    esac
    if [ "${status:-}" = exited ] && [ "${code:-}" = 0 ] && [ "${health:-}" = - ]; then :  # a one-shot (the schema init): done
    elif [ "${status:-}" != running ]; then echo "RED: $n is ${status:-gone} (exit ${code:-?}), not running"; red=1
    elif [ "${health:-}" != - ] && [ "${health:-}" != healthy ]; then echo "RED: $n is ${health:-?}"; red=1
    else running=$((running + 1)); [ "$health" = healthy ] && healthy=$((healthy + 1)); fi
    [ -n "$want" ] && [ "${image:-}" != "$want" ] && { echo "RED: $n runs ${image:-nothing}, not $want"; red=1; }
  done
  if [ "$red" = 0 ]; then
    echo "stack check: ok — $(echo $names | wc -w | tr -d ' ') containers, $running running, $healthy healthy; edge and runner at $tag, backend at ${TS_BACKEND_TAG:-$tag}"
    return 0
  fi
  return 7
}
edge() { # <tag> <on|off>
  local flag=true
  [ "${2:-on}" = "off" ] && flag=false
  AGENT_MARKET_ENABLED=$flag TS_CHAT_EDGE_TAG="$1" ts up -d --no-deps --wait --wait-timeout 300 chat-edge-api | tail -2
  echo "chat-edge $(docker inspect -f '{{.Config.Image}} {{.State.Health.Status}}' sran-test-chat-edge-api-1) · flag $flag · availability $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:14000/api/v1/agent-market/availability)"
}

case "${1:-status}" in
  up)
    # AM24b: any revision git accepts — the tag the built images carry, found by prefix (scripts/ops/rev.sh)
    TAG="$(rev_existing_tag ai-gateway-chat-edge-api "${2:?tag}")"
    # on battery the stack is not brought up (scripts/ops/power.sh); RUN_ON_BATTERY=yes on the command does it anyway
    bash "$ROOT/scripts/ops/power.sh" need "bringing the test stack up" || exit 3
    export TS_BACKEND_TAG="${TS_BACKEND_TAG:-$TAG}" TS_CHAT_EDGE_TAG="$TAG"
    need_images "ai-gateway-chat-edge-api:$TAG" "ai-gateway-backend:$TS_BACKEND_TAG" "ai-gateway-agent-runner:$TAG" "ai-gateway-web-fetcher:${AM_FETCHER_TAG:-$TAG}"
    docker tag "$SANDBOX_FROM" "$SANDBOX_TAG"
    # AM12: bounded — a sync that blocks inside the VM (seen once: 30 minutes, unkillable) must not hold the stack up
    timeout 90 docker run --rm --privileged alpine:3.20 sh -c 'echo 3 > /proc/sys/vm/drop_caches; free -m | head -2' || echo "cache drop skipped"
    ts up -d --no-deps --wait --wait-timeout 300 postgres redis postgres-webchat redis-webchat mail-sink | tail -5
    # AM11: AM7_NO_NER=1 leaves the name-recognition service out (1.1 GB) when the host is short of memory —
    # Agent Market's walks do not need it (the runner's model calls go through the gateway without it)
    NER=ner-service
    [ "${AM7_NO_NER:-0}" = 1 ] && NER=
    ts up -d --no-deps --wait --wait-timeout 600 mock-provider $NER rate-limiter log-worker dlp-sidecar backend gateway backup | tail -8
    runner "$TAG" up -d | tail -4 # the runner first: it migrates
    edge "$TAG" on
    docker logs sran-test-agent-runner --since 3m 2>&1 | grep -E "schema migrated|package registered|agent-runner started|ERROR" | cut -c1-200 | tail -12
    echo "backend $(docker inspect -f '{{.Config.Image}}' sran-test-backend-1) · mail-sink $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18025/api/v1/messages)"
    check "$TAG"; exit $?  # (AM19b: up is not ok because it ran — it is ok because the stack is what was asked)
    ;;
  check) check "$(rev_existing_tag ai-gateway-chat-edge-api "${2:?tag}")"; exit $? ;;
  edge) edge "$(rev_existing_tag ai-gateway-chat-edge-api "${2:?tag}")" "${3:-on}" ;;
  runner)
    docker tag "$SANDBOX_FROM" "$SANDBOX_TAG"
    runner "$(rev_existing_tag ai-gateway-agent-runner "${2:?tag}")" up -d --force-recreate agent-runner | tail -3
    sleep 5
    docker logs sran-test-agent-runner --since 2m 2>&1 | grep -E "schema migrated|package registered|agent-runner started|ERROR" | cut -c1-200 | tail -12
    ;;
  seed)
    python scripts/test_stack_seed.py
    python scripts/agent-market/seed_admins.py
    ;;
  web)
    cd apps/web-chat
    NEXT_PUBLIC_API_URL=http://localhost:14000/api/v1 NEXT_PUBLIC_GATEWAY_URL=http://localhost:18080 AGENT_MARKET_ENABLED=true \
      NEXT_PUBLIC_BUILD_ID="$(rev_tag HEAD)" npx next dev -p 13002
    ;;
  admin)
    KEY="$(dotenv CHAT_EDGE_ADMIN_API_KEY)"
    cd admin-ui
    CHAT_EDGE_API_URL=http://localhost:14000/api/v1 GATEWAY_API_URL=http://localhost:18081 NEXT_PUBLIC_API_BASE_URL=http://localhost:18081 \
      CHAT_EDGE_ADMIN_API_KEY="${KEY:-dev-chat-edge-admin-key-change-in-prod}" npx next dev -p 13000
    ;;
  fresh)
    runner latest down | tail -2
    docker stop sran-test-chat-edge-api-1 >/dev/null 2>&1 || true
    docker exec sran-test-postgres-webchat-1 psql -U chatedge -d sran_ai -v ON_ERROR_STOP=1 -c 'DROP SCHEMA IF EXISTS agent_market CASCADE' | tail -1
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
  *) echo "usage: $0 up <tag>|check <tag>|edge <tag> [on|off]|runner <tag>|seed|web|admin|fresh|status|down" >&2; exit 2 ;;
esac
