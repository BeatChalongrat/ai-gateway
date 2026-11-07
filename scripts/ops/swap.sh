#!/usr/bin/env bash
# AM10 — swap one live service to its new image while a poll measures what a person would have seen.
#
#   bash scripts/ops/swap.sh <service> <probe url> [--image <repository:tag>] <compose args…>
#   e.g. swap.sh chat-edge-api https://localhost:4000/health --image ai-gateway-chat-edge-api:134b023 -p ai-gateway -f docker-compose.webchat.yml
#
# With --image (AM24b) the tool keeps the image the service runs now as <repository>:rollback-<its revision> and
# points the tag the compose files run at the image given; without it, that tag must already name the new image
# and the old one be kept by hand, as before AM24b. The probe is asked every half second from 3 s before the swap until the
# service has answered 200 ten times in a row after it; the result is the count of requests, how many
# did not answer 200, and the longest stretch without a 200.
#
# A service with no door on the host (the runner) is asked through its container's own health check:
#   swap.sh agent-runner health:sran-agent-runner -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market
# and one with neither a door nor a health check (the backup) by whether its container runs: running:<container>.
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
IMAGE=""
if [ "${1:-}" = --image ]; then IMAGE="${2:?--image <repository:tag>}"; shift 2; fi
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
CEILING="${SWAP_CEILING_S:-$(case "$URL" in health:*|running:*) echo 20 ;; *) echo 5 ;; esac)}"
# AM24c: how long the swap waits, after Compose, for ten 200s in a row (SWAP_WAIT_S, 120 s). The script's own tests
# that never answer ask for less: their stand-in sleep returns at once, so the 240 tries were process spawns only —
# about two minutes on this laptop, at the test's own limit, and over it the test hung (see poll below).
WAIT_S="${SWAP_WAIT_S:-120}"
SERIES="${SWAP_SERIES:-$(dirname "$OUT")/swap_series.tsv}"
before=$(docker compose "$@" ps -q "$SERVICE" 2>/dev/null | tr -d '\r' | head -1)
# AM24b — --image: the way back kept by the same command that leaves it. Until AM24b the old image was tagged
# rollback-<revision> by hand before a swap, and a tag forgotten is a way back lost. Refused before anything is
# touched (exit 11): an image not on this host; a compose tag that is a revision's own or a rollback tag (moving it
# would make it lie); for a project that is not live (LIVE_PROJECTS), a compose tag another project also runs —
# the test stack and live share repositories, and a swap on the test stack must never move what live starts next.
# (A live swap is never held up by a test container: it is told, not refused.) Every swap is written to
# results/deploys.tsv, which the rollback runbook (docs/ops/ROLLBACK.md, scripts/ops/rollback_book.py) is checked
# against.
PROJECT=""; prev=""
for a in "$@"; do [ "$prev" = -p ] && PROJECT="$a"; prev="$a"; done
# (the service's own image: `config --images <service>` lists its dependencies' too, in no fixed order; the
# resolved file is read here and only the one name kept)
REF=""
for py in python3 python; do
  command -v "$py" > /dev/null 2>&1 && "$py" -c 'import json' > /dev/null 2>&1 || continue
  REF=$(docker compose "$@" config --format json 2>/dev/null | "$py" -c 'import json, sys
s = json.load(sys.stdin).get("services", {}).get(sys.argv[1]) or {}
print(s.get("image") or "")' "$SERVICE" 2>/dev/null | tr -d '\r')
  break
done
# (no image key: Compose names a built service <project>-<service>, as the running containers show)
[ -n "$REF" ] || [ -z "$PROJECT" ] || REF="$PROJECT-$SERVICE"
case "${REF##*/}" in *:*) ;; ?*) REF="$REF:latest" ;; esac
label_rev() { local r; r=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1" 2>/dev/null | tr -d '\r'); [ "$r" = "<no value>" ] && r=""; printf '%s' "$r"; }
ROLLBACK=""
if [ -n "$IMAGE" ]; then
  refuse() { echo "RED: the swap of $SERVICE was not started: $*"; exit 11; }
  docker image inspect "$IMAGE" > /dev/null 2>&1 || refuse "there is no image $IMAGE on this host"
  [ -n "$REF" ] || refuse "Compose does not say which image $SERVICE runs ($*)"
  if printf '%s' "${REF##*:}" | grep -Eq '^[0-9a-f]{7,40}$|^rollback-'; then
    refuse "the compose files run $REF, a tag that names one image for good — moving it would make it lie"
  fi
  others=$(docker ps -a --format '{{.Names}} {{.Image}} {{.Label "com.docker.compose.project"}}' 2>/dev/null | tr -d '\r' \
    | awk -v a="$REF" -v b="${REF%:latest}" -v p="$PROJECT" '($2 == a || $2 == b) && $3 != p { printf "%s ", $1 }')
  if [ -n "$others" ]; then
    case " ${LIVE_PROJECTS:-ai-gateway sran-agent-market} " in
      *" $PROJECT "*) echo "note: $REF is also what ${others% } of another compose project run — recreated, they start the image given" ;;
      *) refuse "$REF is also what another compose project runs (${others% }) — moving it would change what that project starts next" ;;
    esac
  fi
  new_id=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null | tr -d '\r')
  old_id=""
  [ -z "$before" ] || old_id=$(docker inspect -f '{{.Image}}' "$before" 2>/dev/null | tr -d '\r')
  [ -z "$old_id" ] || [ "$old_id" != "$new_id" ] || refuse "$SERVICE already runs $IMAGE"
  if [ -n "$old_id" ]; then
    old_rev=$(label_rev "$old_id")
    ROLLBACK="${REF%:*}:rollback-${old_rev:-$(date -u +%Y%m%d%H%M%S)}"
    docker tag "$old_id" "$ROLLBACK" || refuse "the image it runs now could not be kept as $ROLLBACK"
    echo "rollback image: $ROLLBACK (what $SERVICE runs now)"
  fi
  docker tag "$IMAGE" "$REF" || refuse "$REF could not be pointed at $IMAGE"
  echo "$REF now names $IMAGE"
fi
ask() { # 200 when the service answers: its door, or (health:<container>) what Docker's health check says of it
  case "$URL" in
    health:*) [ "$(docker inspect -f '{{.State.Health.Status}}' "${URL#health:}" 2>/dev/null)" = healthy ] && echo 200 || echo 000 ;;
    running:*) [ "$(docker inspect -f '{{.State.Status}}' "${URL#running:}" 2>/dev/null)" = running ] && echo 200 || echo 000 ;;
    *) curl -sk -o /dev/null -m 3 -w '%{http_code}' "$URL" ;;
  esac
}
EVENTS="$OUT.events"
: > "$EVENTS" 2>/dev/null
docker events --filter type=container --filter "label=com.docker.compose.service=$SERVICE" \
  --format '{{.TimeNano}} {{.Action}} {{.Actor.ID}}' >> "$EVENTS" 2>/dev/null &
EV=$!
# (AM24c: the poll stops by itself once the swap is gone. A swap killed the hard way runs no EXIT trap; until AM24c
# its poll lived on — asking the service every half second, for good, and holding the caller's output open: the host
# tests' lane waited on such a pipe until its ceiling, and the loop went on spinning after it.)
poll() { while kill -0 $$ 2>/dev/null; do printf '%s %s\n' "$(date +%s.%N)" "$(ask)" >> "$OUT"; sleep 0.5; done; }
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
for _ in $(seq 1 $((WAIT_S * 2))); do
  if [ "$(tail -1 "$OUT" | awk '{print $2}')" = 200 ]; then ok=$((ok + 1)); else ok=0; fi
  [ "$ok" -ge 10 ] && break
  sleep 0.5
done
sleep 1 # (the new container's last events)
kill $POLL $EV 2>/dev/null
rc=0
[ "$ok" -ge 10 ] || { echo "RED: $SERVICE did not answer 200 ten times in a row within $WAIT_S s of the swap"; rc=9; }
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
# AM24b: what runs now, for the rollback runbook — time, tool, project, service, image asked, image id, revision,
# the rollback image kept, verdict
RECORD="${DEPLOY_RECORD:-$(dirname "$OUT")/deploys.tsv}"
now_id=$(docker inspect -f '{{.Image}}' "$after" 2>/dev/null | tr -d '\r')
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" swap "${PROJECT:--}" "$SERVICE" "${IMAGE:-${REF:--}}" \
  "${now_id:--}" "$(label_rev "${now_id:-none}")" "${ROLLBACK:--}" "$verdict" >> "$RECORD" 2>/dev/null \
  || echo "(the swap could not be recorded in $RECORD)"
[ -z "$ROLLBACK" ] || echo "back with: bash scripts/ops/swap.sh $SERVICE $URL --image $ROLLBACK $*"
case "$PROJECT" in ai-gateway|sran-agent-market)
  echo "the rollback runbook now names what ran before: python scripts/ops/rollback_book.py write, then commit docs/ops/ROLLBACK.md" ;;
esac
echo "swap $SERVICE done $(date -u +%H:%M:%SZ)$([ $rc = 0 ] || echo " — RED (exit $rc)")$slow"
exit $rc
