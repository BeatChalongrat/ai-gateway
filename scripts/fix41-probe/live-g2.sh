#!/usr/bin/env bash
# FIX41 G2 — fire the three cases through a LIVE chat-edge container.
#
# The container is the freshly built production image, on the real
# postgres-webchat and redis-webchat, reached over real HTTP. Its
# GATEWAY_API_URL points at scripts/fix41-probe/fake-gateway.js because
# every provider key on this box is the placeholder `sk-...` (see that
# file for why the documented mock-provider redirect was NOT used).
#
#   node scripts/fix41-probe/fake-gateway.js 9199 &
#   docker run ... -p 4010:4000 ai-gateway-chat-edge-api:latest
#   scripts/fix41-probe/live-g2.sh
#
# Raw frames land in scripts/fix41-probe/results/after/live-*.txt
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
API="${FIX41_LIVE_API:-http://localhost:4010/api/v1}"
OUT="$REPO/scripts/fix41-probe/results/after"
mkdir -p "$OUT"

SEC="$(docker exec ai-gateway-chat-edge-api env 2>/dev/null | grep '^PORTAL_JWT_SECRET=' | cut -d= -f2-)"
TOKEN="$(PORTAL_JWT_SECRET="$SEC" node "$REPO/scripts/fix33-probe/mint-jwt.js" \
  c6e631e2-51ab-44a4-9eed-45d6fc1ce9ff f65614b9-3328-5d45-af3a-9ebc2b6d3034 fix41@local)"

newconv() {
  curl -s -m 20 -X POST "$API/conversations" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"title":"FIX41 live","model_id":"gpt-4o-mini"}' |
    sed -E 's/.*"id":"([^"]+)".*/\1/'
}

# ── case 1: an ordinary turn, start to finish ──────────────────────────────
C1="$(newconv)"
echo "case 1 (a normal turn) conversation=$C1"
curl -s -m 60 -N -X POST "$API/conversations/$C1/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"เล่าอะไรสักอย่างสั้น ๆ","stream":true}' >"$OUT/live-1-normal.txt"
echo "  → $OUT/live-1-normal.txt"

# ── case 2: the same turn, interrupted and resumed with a cursor ───────────
C2="$(newconv)"
echo "case 2 (disconnect, then resume with starting_after) conversation=$C2"
curl -s -m 2 -N -X POST "$API/conversations/$C2/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"เล่าอะไรสักอย่างสั้น ๆ","stream":true}' >"$OUT/live-2a-prefix.txt"
MSG="$(grep -m1 '^data: ' "$OUT/live-2a-prefix.txt" | sed -E 's/.*"message_id":"([^"]+)".*/\1/')"
LAST="$(grep -o '"seq":[0-9]*' "$OUT/live-2a-prefix.txt" | tail -1 | cut -d: -f2)"
echo "  dropped after seq $LAST (message $MSG); reattaching with starting_after=$LAST"
curl -s -m 60 -N "$API/conversations/$C2/messages/$MSG/stream?starting_after=$LAST" \
  -H "Authorization: Bearer $TOKEN" >"$OUT/live-2b-resume.txt"
echo "  → $OUT/live-2b-resume.txt"

# ── case 3: a turn a person stops ─────────────────────────────────────────
C3="$(newconv)"
echo "case 3 (cancel) conversation=$C3"
curl -s -m 60 -N -X POST "$API/conversations/$C3/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"เล่าอะไรสักอย่างสั้น ๆ","stream":true}' >"$OUT/live-3-cancel.txt" &
STREAM_PID=$!
sleep 1.2
echo -n "  POST /cancel #1: "
curl -s -m 10 -X POST "$API/conversations/$C3/cancel" -H "Authorization: Bearer $TOKEN"
echo
echo -n "  POST /cancel #2: "
curl -s -m 10 -X POST "$API/conversations/$C3/cancel" -H "Authorization: Bearer $TOKEN"
echo
wait $STREAM_PID
echo "  → $OUT/live-3-cancel.txt"
