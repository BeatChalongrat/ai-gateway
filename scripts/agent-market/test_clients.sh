#!/usr/bin/env bash
# AM10 — the test stack's two web clients as production builds in containers, where AM7's test_stack.sh
# starts two Next dev servers (`web`, `admin`). A dev server holds 1–2 GB each on the host for as long as
# a walk lasts; this machine has no such memory beside the live stack. A built app holds about 100 MB,
# and it is the build a person gets (no dev overlay, no StrictMode double effects).
#
#   bash scripts/agent-market/test_clients.sh build [rev]   # images from a clean archive of <rev> (default HEAD)
#   bash scripts/agent-market/test_clients.sh up [rev]      # web-chat on :13002, admin-ui on :13000
#   bash scripts/agent-market/test_clients.sh down          # the two containers removed (images kept)
#
# The apps are built for the test stack's own doors (chat-edge 14000, gateway 18080, backend 18081 —
# all loopback) and nothing else: the image names say so (am10-test-*), they are never tagged as the
# images the live compose files run, and the containers join the test project's network only.
set -euo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
REV="$(git rev-parse --short=7 "${2:-HEAD}")"
WEB_IMG="am10-test-web-chat:$REV"
ADMIN_IMG="am10-test-admin-ui:$REV"
NET=sran-test_default
dotenv() { grep "^$1=" .env | cut -d= -f2- | tr -d '\r\n'; }

case "${1:-}" in
  build)
    # on battery nothing is built (scripts/ops/power.sh); RUN_ON_BATTERY=yes on the command runs it anyway
    bash scripts/ops/power.sh need "the test clients' build" || exit 3
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    git archive "$REV" apps/web-chat admin-ui | tar -x -C "$TMP_DIR"
    CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    docker build -q -t "$WEB_IMG" \
      --build-arg NEXT_PUBLIC_API_URL=http://localhost:14000/api/v1 \
      --build-arg NEXT_PUBLIC_GATEWAY_URL=http://localhost:18080 \
      --build-arg NEXT_PUBLIC_BUILD_ID="$REV" --build-arg REVISION="$REV" --build-arg CREATED="$CREATED" \
      "$(native "$TMP_DIR/apps/web-chat")" >/dev/null
    echo "built $WEB_IMG revision=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$WEB_IMG")"
    docker build -q -t "$ADMIN_IMG" \
      --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:18081 \
      --build-arg REVISION="$REV" --build-arg CREATED="$CREATED" \
      "$(native "$TMP_DIR/admin-ui")" >/dev/null
    echo "built $ADMIN_IMG revision=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$ADMIN_IMG")"
    ;;
  up)
    docker rm -f sran-test-am10-web sran-test-am10-admin >/dev/null 2>&1 || true
    docker run -d --name sran-test-am10-web --memory 768m --oom-score-adj 500 --network "$NET" -p 127.0.0.1:13002:3002 \
      -e PORT=3002 -e AGENT_MARKET_ENABLED=true "$WEB_IMG" >/dev/null
    KEY="$(dotenv CHAT_EDGE_ADMIN_API_KEY)"
    docker run -d --name sran-test-am10-admin --memory 768m --oom-score-adj 500 --network "$NET" -p 127.0.0.1:13000:3000 \
      -e PORT=3000 -e HOSTNAME=0.0.0.0 -e NEXT_PUBLIC_API_BASE_URL=http://localhost:18081 \
      -e GATEWAY_API_URL=http://backend:8080 -e CHAT_EDGE_API_URL=http://chat-edge-api:4000/api/v1 \
      -e CHAT_EDGE_ADMIN_API_KEY="${KEY:-dev-chat-edge-admin-key-change-in-prod}" "$ADMIN_IMG" >/dev/null
    for i in $(seq 1 40); do
      w=$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://localhost:13002/chat || true)
      a=$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://localhost:13000/login || true)
      [ "$w" != 000 ] && [ "$a" != 000 ] && break
      sleep 1
    done
    echo "web-chat $WEB_IMG → :13002 /chat $w · admin-ui $ADMIN_IMG → :13000 /login $a"
    ;;
  down)
    docker rm -f sran-test-am10-web sran-test-am10-admin 2>/dev/null | tr '\n' ' '
    echo "removed"
    ;;
  *) echo "usage: $0 build [rev] | up [rev] | down" >&2; exit 2 ;;
esac
