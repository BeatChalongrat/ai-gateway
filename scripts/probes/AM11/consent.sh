#!/usr/bin/env bash
# AM11 (E) — the TEST stack's backend pointed at a stand-in consent page, so the page a connector's
# consent flow ends on can be walked in a browser (walk.js consent web|desktop) with no Google client.
#
#   bash scripts/probes/AM11/consent.sh up     # the stand-in (sran-test-am11-consent, loopback :18099) and
#                                              # the test backend recreated with four settings
#   bash scripts/probes/AM11/consent.sh down   # the stand-in removed, the test backend as it was
#   bash scripts/probes/AM11/consent.sh status
#
# The four settings (a made-up client id and secret; where "Google" is) reach the test project's
# backend only, through a compose override written to a temp file. The browser is sent to the
# stand-in on the host's loopback; the backend trades the code with it inside the test network.
# Nothing here names a container, a volume or a port of the live project, or a real provider.
set -euo pipefail
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
NET=sran-test_default
NAME=sran-test-am11-consent
BACKEND=sran-test-backend-1
# the backend is recreated as it runs now: the same image, the same origins
envof() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BACKEND" | grep "^$1=" | cut -d= -f2- | tr -d '\r\n'; }
recreate() { # [override file]
  local tag cors
  tag="$(docker inspect -f '{{.Config.Image}}' "$BACKEND" | sed 's/.*://')"
  cors="$(envof CORS_ORIGINS)"
  TS_BACKEND_TAG="$tag" CORS_ORIGINS="$cors" NER_WORKERS=1 \
    docker compose -p sran-test -f docker-compose.test-stack.yml ${1:+-f "$1"} up -d --no-deps --wait --wait-timeout 300 backend 2>&1 | grep -v "level=warning" | tail -2
  echo "backend $(docker inspect -f '{{.Config.Image}} {{.State.Health.Status}}' "$BACKEND")"
}

case "${1:-status}" in
  up)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" --network "$NET" -p 127.0.0.1:18099:8099 \
      -v "$(native "$ROOT/scripts/probes/AM11/fake_consent.js"):/app/fake_consent.js:ro" node:20 node /app/fake_consent.js >/dev/null
    OVR="$(mktemp --suffix=.yml)"
    trap 'rm -f "$OVR"' EXIT
    cat > "$OVR" <<EOF
services:
  backend:
    environment:
      CONNECTOR_OAUTH_GOOGLE_CLIENT_ID: "standin-client.test"
      CONNECTOR_OAUTH_GOOGLE_CLIENT_SECRET: "standin-secret-not-a-secret"
      CONNECTOR_OAUTH_GOOGLE_AUTHORIZE_URL: "http://localhost:18099/authorize"
      CONNECTOR_OAUTH_GOOGLE_TOKEN_URL: "http://$NAME:8099/token"
EOF
    recreate "$(native "$OVR")"
    for i in $(seq 1 20); do
      [ "$(curl -s -o /dev/null -m 2 -w '%{http_code}' http://127.0.0.1:18099/health || true)" = 200 ] && break
      sleep 1
    done
    echo "stand-in consent $(curl -s -m 3 http://127.0.0.1:18099/health || echo down) on :18099 · backend knows it: $(envof CONNECTOR_OAUTH_GOOGLE_AUTHORIZE_URL)"
    ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    recreate
    echo "stand-in removed · backend's authorize address: '$(envof CONNECTOR_OAUTH_GOOGLE_AUTHORIZE_URL)'"
    ;;
  status)
    echo "stand-in: $(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || echo none) · backend's authorize address: '$(envof CONNECTOR_OAUTH_GOOGLE_AUTHORIZE_URL)'"
    ;;
  *) echo "usage: $0 up | down | status" >&2; exit 2 ;;
esac
