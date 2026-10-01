#!/usr/bin/env bash
# AM9 (I) — "send as me" end to end without anyone's real mailbox: the real MCP gateway image (its
# JWT-checked proxy, the per-person child, servers/mail-send.js) on a private network with a stand-in
# for the backend's connector broker and the Gmail API (fake_google.js), driven by chat-edge's own mail
# client (TestAM9MailEndToEndThroughTheRealGateway). A real working paper is attached and read back.
# Nothing of the live or test stack is touched; the network and containers are removed at the end.
#
#   bash scripts/probes/AM9/mail_e2e.sh <gateway-image-tag>   > scripts/probes/AM9/results/mail_e2e.txt
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:?gateway image tag}"
OUT="$ROOT/scripts/probes/AM9/results/mail_e2e"
FILE="$ROOT/scripts/probes/AM9/results/print/bank_reconciliation.xlsx"
NET=am9-mail-e2e
SECRET="e2e-portal-secret-$(date +%s)"
INTERNAL="e2e-internal-$(date +%s)"
w() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
rm -rf "$OUT" && mkdir -p "$OUT"
cleanup() { docker rm -f am9-fake-google am9-mcp-gateway >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1; }
trap cleanup EXIT
cleanup
docker network create "$NET" >/dev/null
MSYS_NO_PATHCONV=1 docker run -d --name am9-fake-google --network "$NET" --network-alias fake-google -p 127.0.0.1:18099:8099 \
  -v "$(w "$ROOT/scripts/probes/AM9"):/probe:ro" -v "$(w "$OUT"):/out" node:20-alpine node /probe/fake_google.js >/dev/null
MSYS_NO_PATHCONV=1 docker run -d --name am9-mcp-gateway --network "$NET" -p 127.0.0.1:13200:3200 \
  -e PORTAL_JWT_SECRET="$SECRET" -e BACKEND_BASE=http://fake-google:8099 -e MCP_INTERNAL_TOKEN="$INTERNAL" \
  -e MCP_MAIL_API_BASE=http://fake-google:8099 "ai-gateway-mcp-gateway:$TAG" >/dev/null
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:13200/health >/dev/null && break; sleep 1; done
echo "gateway $(docker inspect am9-mcp-gateway --format '{{index .Config.Labels "org.opencontainers.image.revision"}}') up"
echo "catalogue lists: $(curl -s http://127.0.0.1:13200/catalog.json | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>console.log(JSON.parse(s).connectors.map(c=>c.id).join(",")))')"
cd "$ROOT/services/chat-edge-api" && AM9_MAIL_E2E_GATEWAY=http://127.0.0.1:13200 AM9_MAIL_E2E_PORTAL=http://127.0.0.1:18099 \
  AM9_MAIL_E2E_JWT_SECRET="$SECRET" AM9_MAIL_E2E_INTERNAL="$INTERNAL" AM9_MAIL_E2E_FILE="$(w "$FILE")" \
  AM9_MAIL_E2E_RAW="$(w "$OUT/raw.eml")" go test ./internal/agentmarket/ -run TestAM9MailEndToEndThroughTheRealGateway -count=1 -v 2>&1 \
  | grep -E "^(--- |ok|FAIL)|ASSERT|am9_mail_e2e_test"
echo "broker: $(cat "$OUT/broker.log" 2>/dev/null | tail -1)"
echo "gateway log mentions the token: $(docker logs am9-mcp-gateway 2>&1 | grep -c 'ya29.e2e' || true)"
