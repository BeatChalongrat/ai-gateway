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
#
# AM24 — and a swap that swapped nothing is not a pass. On 16 Oct 2026 the admin's swap was run against the web
# chat's compose file: Compose said "no such service", nothing was recreated, the old admin went on answering 200,
# and the tool printed a pass. Now the compose files are asked which services they hold before anything is
# touched (a service they do not name: RED, exit 6, naming the service and the files); Compose refusing the swap
# is RED (exit 7); a service still running the container it ran before is RED (exit 10: nothing was swapped).
# Every swap is told against a ceiling, the longest stretch without a 200 it should not reach (SWAP_CEILING_S:
# 5 s for a door, 20 s for a health check — see the series below), and over it says SLOW, loudly, with where
# the time went: the old container's stop, the new one's start and health, from Docker's events. A slow swap
# is not a failed one: it exits 0, and it is a thing to tell the owner. Every measured swap is added to
# results/swap_series.tsv, so a gap that grows is seen at 2×, not at 23×.
set -uo pipefail
SERVICE="${1:?service}"; URL="${2:?probe url}"; shift 2
bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/power.sh" need "the swap of $SERVICE" || exit 3
OUT="${SWAP_OUT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/results/swap_${SERVICE}.txt}" # (SWAP_OUT: the script's own test)
mkdir -p "$(dirname "$OUT")" 2>/dev/null
{ : > "$OUT"; } 2>/dev/null || { echo "the swap of $SERVICE was not started: its measurement cannot be written ($OUT)"; exit 5; }
# AM24: the compose files must hold the service — asked before anything is touched
held=$(docker compose "$@" config --services 2>&1); st=$?
held=$(printf '%s\n' "$held" | tr -d '\r' | grep -v "level=warning")
if [ "$st" -ne 0 ]; then
  echo "RED: the swap of $SERVICE was not started: Compose cannot read the files given ($*): $(printf '%s' "$held" | head -1)"
  exit 6
fi
if ! printf '%s\n' "$held" | grep -qx -- "$SERVICE"; then
  echo "RED: the swap of $SERVICE was not started: the compose files given ($*) hold no service named $SERVICE (they hold: $(printf '%s' "$held" | tr '\n' ' '))"
  exit 6
fi
CEILING="${SWAP_CEILING_S:-$(case "$URL" in health:*) echo 20 ;; *) echo 5 ;; esac)}"
SERIES="${SWAP_SERIES:-$(dirname "$OUT")/swap_series.tsv}"
before=$(docker compose "$@" ps -q "$SERVICE" 2>/dev/null | tr -d '\r' | head -1)
ask() { # 200 when the service answers: its door, or (health:<container>) what Docker's health check says of it
  case "$URL" in
    health:*) [ "$(docker inspect -f '{{.State.Health.Status}}' "${URL#health:}" 2>/dev/null)" = healthy ] && echo 200 || echo 000 ;;
    *) curl -sk -o /dev/null -m 3 -w '%{http_code}' "$URL" ;;
  esac
}
EVENTS="$OUT.events"
: > "$EVENTS" 2>/dev/null
docker events --filter type=container --filter "label=com.docker.compose.service=$SERVICE" \
  --format '{{.TimeNano}} {{.Action}} {{.Actor.ID}}' >> "$EVENTS" 2>/dev/null &
EV=$!
poll() { while :; do printf '%s %s\n' "$(date +%s.%N)" "$(ask)" >> "$OUT"; sleep 0.5; done; }
poll & POLL=$!
trap 'kill $POLL $EV 2>/dev/null' EXIT
sleep 3
echo "swap $SERVICE starts $(date -u +%H:%M:%SZ)"
up=$(docker compose "$@" up -d --no-deps "$SERVICE" 2>&1); upst=$?
printf '%s\n' "$up" | grep -v "level=warning" | tail -3
if [ "$upst" -ne 0 ]; then
  echo "RED: Compose refused the swap of $SERVICE (exit $upst): $(printf '%s' "$up" | grep -v "level=warning" | tail -1)"
  exit 7
fi
after=$(docker compose "$@" ps -q "$SERVICE" 2>/dev/null | tr -d '\r' | head -1)
if [ -n "$before" ] && [ "$before" = "$after" ]; then
  echo "RED: nothing was swapped: $SERVICE still runs the container it ran before (${before:0:12}) — is :latest the image it already ran?"
  exit 10
fi
ok=0
for _ in $(seq 1 240); do
  if [ "$(tail -1 "$OUT" | awk '{print $2}')" = 200 ]; then ok=$((ok + 1)); else ok=0; fi
  [ "$ok" -ge 10 ] && break
  sleep 0.5
done
sleep 1 # (the new container's last events)
kill $POLL $EV 2>/dev/null
rc=0
[ "$ok" -ge 10 ] || { echo "RED: $SERVICE did not answer 200 ten times in a row within 120 s of the swap"; rc=9; }
# (the figures, from what the poll recorded — and NOT MEASURED when it recorded nothing: that is not a pass)
figures=$(awk '{ n++; if ($2 != 200) { bad++; if (!start) start = $1; last = $1 } else if (start) { w = last - start + 0.5; if (w > worst) worst = w; start = 0 } }
     END { if (start) { w = last - start + 0.5; if (w > worst) worst = w }
           if (n == 0) { print "NOT MEASURED: the poll recorded nothing in " FILENAME; exit 1 }
           printf "%d %d %.1f\n", n, bad, worst }' "$OUT")
if [ $? -ne 0 ]; then
  echo "$figures"
  echo "swap $SERVICE done $(date -u +%H:%M:%SZ) — RED (exit 8)"
  exit 8
fi
read -r n bad worst <<< "$figures"
echo "probe: $n requests, $bad not 200, longest stretch without a 200: $worst s (ceiling $CEILING s)"
# where the time went, from Docker's events: the old container's stop (kill → die), the new one's start (create →
# start: Compose starts the new container once the old one is gone) and its health (start → healthy); "-" where
# Docker said nothing
read -r stop_s start_s healthy_s <<< "$(awk -v old="$before" -v new="$after" '
  function s(a, b) { return (a && b) ? sprintf("%.1f", (b - a) / 1e9) : "-" }
  $3 == old && $2 == "kill" && !k { k = $1 }
  $3 == old && $2 == "die" && !d { d = $1 }
  $3 == new && $2 == "create" && !c { c = $1 }
  $3 == new && $2 == "start" && !st { st = $1 }
  $2 == "health_status:" && $3 == "healthy" && $4 == new && !h { h = $1 }
  END { printf "%s %s %s\n", s(k, d), s(c, st), s(st, h) }' "$EVENTS" 2>/dev/null || echo "- - -")"
echo "where the time went: the old container stopped in ${stop_s:--} s; the new one started ${start_s:--} s after it was made, and was healthy ${healthy_s:--} s after its start"
slow=""
if awk -v w="$worst" -v c="$CEILING" 'BEGIN { exit !(w > c) }'; then
  slow=" — SLOW: $worst s without a 200, over the ceiling of $CEILING s"
  echo "SLOW SWAP: $SERVICE did not answer for $worst s, over its ceiling of $CEILING s. A slow swap is not a failed one — but tell the owner, with where the time went (above)."
fi
verdict=$([ -n "$slow" ] && echo slow || echo ok)
[ $rc = 0 ] || verdict="red$rc"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SERVICE" "${before:0:12}" "${after:0:12}" \
  "$n" "$bad" "$worst" "$CEILING" "${stop_s:--}" "${start_s:--}" "${healthy_s:--}" "$verdict" >> "$SERIES" 2>/dev/null \
  || echo "(the swap's line could not be added to $SERIES)"
echo "swap $SERVICE done $(date -u +%H:%M:%SZ)$([ $rc = 0 ] || echo " — RED (exit $rc)")$slow"
exit $rc
