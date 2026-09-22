#!/usr/bin/env bash
# FIX128 C — the separate test stack (compose project `sran-test`).
#
#   scripts/test-stack.sh up       # regenerate + start, pinned to the live stack's image revisions
#   scripts/test-stack.sh seed     # tenant/user/models (incl. a dead one) — scripts/test_stack_seed.py
#   scripts/test-stack.sh status   # containers, ports, image revisions
#   scripts/test-stack.sh down     # stop and remove containers (volumes kept)
#   scripts/test-stack.sh reset    # down + delete the test volumes (a clean start next `up`)
#
# Every destructive test (delete / network drop / restart / dead model) runs
# here first — docs/testing/test-stack.md. Nothing here touches a container,
# volume or port of the live project (`ai-gateway`): see the generator's
# LIVE_REFERENCES guard and the `live-check` sub-command.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
P=sran-test
F=docker-compose.test-stack.yml

rev() { docker inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true; }

compose() {
  TS_BACKEND_TAG="${TS_BACKEND_TAG:-$(rev ai-gateway-backend-1)}" \
  TS_GATEWAY_TAG="${TS_GATEWAY_TAG:-$(rev ai-gateway-gateway-1)}" \
  TS_CHAT_EDGE_TAG="${TS_CHAT_EDGE_TAG:-$(rev ai-gateway-chat-edge-api)}" \
    docker compose -p "$P" -f "$F" "$@"
}

live_snapshot() { # counts that must not move while the test stack runs
  local out="$1"
  {
    echo "at=$(date -u +%FT%TZ)"
    echo "live_containers=$(docker ps --filter label=com.docker.compose.project=ai-gateway --format '{{.Names}} {{.ID}} {{.Status}}' | sort | sed 's/ (healthy)//; s/Up [^ ]* [^ ]*//' | md5sum | cut -c1-12)"
    echo "chat_convs=$(docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai -At -c 'select count(*) from chat.conversations')"
    echo "chat_msgs=$(docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai -At -c 'select count(*) from chat.messages_masked')"
    echo "gw_request_logs=$(docker exec ai-gateway-postgres-1 psql -U postgres -d ai_gateway -At -c 'select count(*) from request_logs')"
    echo "gw_tenants=$(docker exec ai-gateway-postgres-1 psql -U postgres -d ai_gateway -At -c 'select count(*) from tenants')"
    echo "gw_model_configs=$(docker exec ai-gateway-postgres-1 psql -U postgres -d ai_gateway -At -c 'select count(*) || chr(47) || md5(string_agg(model || is_active::text, chr(44) order by model)) from model_configs')"
    echo "redis_health_keys=$(docker exec ai-gateway-redis-1 sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "provider:*" | wc -l')"
  } > "$out"
}

case "${1:-status}" in
  up)
    python scripts/test_stack_gen.py
    compose up -d --wait --wait-timeout 300
    compose ps --format '{{.Service}} {{.Status}}'
    ;;
  seed)
    python scripts/test_stack_seed.py
    ;;
  status)
    compose ps --format '{{.Service}} | {{.Status}} | {{.Ports}}'
    for c in $(compose ps -q); do echo "$(docker inspect "$c" --format '{{.Name}} {{.Config.Image}}')"; done
    ;;
  live-check)
    live_snapshot "${2:-/dev/stdout}"
    ;;
  down)
    compose down --remove-orphans
    ;;
  reset)
    compose down --remove-orphans --volumes
    ;;
  *)
    echo "usage: $0 up|seed|status|live-check [file]|down|reset" >&2
    exit 2
    ;;
esac
