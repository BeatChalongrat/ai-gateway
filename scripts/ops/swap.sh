#!/usr/bin/env bash
# AM10 — swap one live service to its new image while a poll measures what a person would have seen.
#
#   bash scripts/ops/swap.sh <service> <probe url> <compose args…>
#   e.g. swap.sh chat-edge-api https://localhost:4000/health -p ai-gateway -f docker-compose.webchat.yml
#
# The image must already be tagged :latest (the tag the compose file runs) and the old one
# :rollback-<its revision>. The probe is asked every half second from 3 s before the swap until the
# service has answered 200 ten times in a row after it; the result is the count of requests, how many
# did not answer 200, and the longest stretch without a 200.
#
# A service with no door on the host (the runner) is asked through its container's own health check:
#   swap.sh agent-runner health:sran-agent-runner -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market
#
# On battery the swap is refused and nothing is touched (scripts/ops/power.sh): a swap the battery cuts
# short leaves live between two images. RUN_ON_BATTERY=yes on the command runs it anyway.
#
# AM19b — the verdict is what was measured, never what was intended. On 14 Oct 2026 the results folder
# did not exist at the tool's new home: the poll could not write, the swap of the web chat went
# unmeasured, and "0 requests, 0 not 200" printed as if it were a pass. Now: a swap whose measurement
# cannot be written is not started (exit 5, live untouched); a swap the poll recorded nothing of says
# NOT MEASURED (exit 8); a service that never answers 200 ten times in a row within 120 s is RED (exit 9).
set -uo pipefail
SERVICE="${1:?service}"; URL="${2:?probe url}"; shift 2
bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/power.sh" need "the swap of $SERVICE" || exit 3
OUT="${SWAP_OUT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/results/swap_${SERVICE}.txt}" # (SWAP_OUT: the script's own test)
mkdir -p "$(dirname "$OUT")" 2>/dev/null
{ : > "$OUT"; } 2>/dev/null || { echo "the swap of $SERVICE was not started: its measurement cannot be written ($OUT)"; exit 5; }
ask() { # 200 when the service answers: its door, or (health:<container>) what Docker's health check says of it
  case "$URL" in
    health:*) [ "$(docker inspect -f '{{.State.Health.Status}}' "${URL#health:}" 2>/dev/null)" = healthy ] && echo 200 || echo 000 ;;
    *) curl -sk -o /dev/null -m 3 -w '%{http_code}' "$URL" ;;
  esac
}
poll() { while :; do printf '%s %s\n' "$(date +%s.%N)" "$(ask)" >> "$OUT"; sleep 0.5; done; }
poll & POLL=$!
trap 'kill $POLL 2>/dev/null' EXIT
sleep 3
echo "swap $SERVICE starts $(date -u +%H:%M:%SZ)"
docker compose "$@" up -d --no-deps "$SERVICE" 2>&1 | grep -v "level=warning" | tail -3
ok=0
for _ in $(seq 1 240); do
  if [ "$(tail -1 "$OUT" | awk '{print $2}')" = 200 ]; then ok=$((ok + 1)); else ok=0; fi
  [ "$ok" -ge 10 ] && break
  sleep 0.5
done
kill $POLL 2>/dev/null
rc=0
[ "$ok" -ge 10 ] || { echo "RED: $SERVICE did not answer 200 ten times in a row within 120 s of the swap"; rc=9; }
# (the figures, from what the poll recorded — and NOT MEASURED when it recorded nothing: that is not a pass)
awk '{ n++; if ($2 != 200) { bad++; if (!start) start = $1; last = $1 } else if (start) { w = last - start + 0.5; if (w > worst) worst = w; start = 0 } }
     END { if (start) { w = last - start + 0.5; if (w > worst) worst = w }
           if (n == 0) { print "NOT MEASURED: the poll recorded nothing in " FILENAME; exit 1 }
           printf "probe: %d requests, %d not 200, longest stretch without a 200: %.1f s\n", n, bad, worst }' "$OUT" || rc=8
echo "swap $SERVICE done $(date -u +%H:%M:%SZ)$([ $rc = 0 ] || echo " — RED (exit $rc)")"
exit $rc
