#!/usr/bin/env bash
# AM13 — how long the front door keeps a service away after it restarts. Not live: a front door of its own
# (the same image and version live runs), one stand-in service behind it, and a client that asks four times
# a second and waits ten seconds for an answer, as a browser does. The stand-in is stopped, kept away for
# 3.5 seconds (what web-chat took on 7 Oct 2026) and started again.
#
#   bash scripts/probes/AM13/front_door_gap.sh old     the settings live has (fail_duration 15s, health every 5s)
#   bash scripts/probes/AM13/front_door_gap.sh new     the settings proposed for a service with one copy
#   bash scripts/probes/AM13/front_door_gap.sh down    remove what it started
#
# Prints: requests asked, how many were not answered 200, the longest answer, and the stretch of time from
# the first request that was not answered 200 to the first that was again.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || pwd)"
OUT="$HERE/results/front_door"; mkdir -p "$OUT"
NET=am13-door; UP=am13-door-up; CADDY=am13-door-caddy; ASK=am13-door-ask
down() { docker rm -f "$UP" "$CADDY" "$ASK" > /dev/null 2>&1; docker network rm "$NET" > /dev/null 2>&1; true; }
MODE="${1:-old}"
[ "$MODE" = down ] && { down; echo removed; exit 0; }
case "$MODE" in
  old) SET='lb_policy least_conn
		lb_try_duration 5s
		lb_try_interval 250ms
		health_interval 5s
		health_timeout 5s
		fail_duration 15s
		max_fails 2
		unhealthy_status 5xx' ;;
  new) SET='lb_policy first
		lb_try_duration 10s
		lb_try_interval 250ms
		health_interval 1s
		health_timeout 5s
		fail_duration 0
		unhealthy_status 5xx' ;;
  *) echo "old | new | down"; exit 2 ;;
esac
down
cat > "$OUT/Caddyfile.$MODE" <<EOF
{
	admin off
	auto_https off
}
:8080 {
	reverse_proxy web-chat:3002 {
		$SET
		health_uri /api/version
		transport http {
			dial_timeout 5s
			response_header_timeout 300s
			keepalive 90s
		}
	}
}
EOF
docker network create --internal "$NET" > /dev/null
# (the stand-in and the client are Python from an image already on this host: alpine's busybox has no httpd
# applet and its date has no milliseconds)
PY="${FRONT_DOOR_PY_IMAGE:-ai-gateway-backend:latest}"
upstream() { MSYS_NO_PATHCONV=1 docker run -d --name "$UP" --network "$NET" --network-alias web-chat --memory 128m --oom-score-adj 1000   --entrypoint sh "$PY" -c 'mkdir -p /tmp/w/api && echo ok > /tmp/w/api/version && exec python -m http.server 3002 --directory /tmp/w' > /dev/null; }
upstream
MSYS_NO_PATHCONV=1 docker run -d --name "$CADDY" --network "$NET" --memory 128m --oom-score-adj 1000 \
  -v "$OUT/Caddyfile.$MODE:/etc/caddy/Caddyfile:ro" caddy:2-alpine > /dev/null
sleep 8 # (the front door's first health checks)
# the client: one line per request — when it was asked (ms), how long the answer took (ms), 200 or not
MSYS_NO_PATHCONV=1 docker run -d --name "$ASK" --network "$NET" --memory 128m --oom-score-adj 1000 --entrypoint python "$PY" -u -c '
import time, urllib.request
for _ in range(140):
    a = time.time()
    try:
        r = urllib.request.urlopen("http://'"$CADDY"':8080/api/version", timeout=10).status
    except Exception:
        r = "ERR"
    print(int(a * 1000), int((time.time() - a) * 1000), r, flush=True)
    time.sleep(0.25)
' > /dev/null
sleep 4
T0=$(date +%s%3N)
docker rm -f "$UP" > /dev/null 2>&1      # the old container goes
sleep 3.5                                 # what web-chat took to be ready again
upstream                                  # the new one answers
for _ in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Running}}' "$ASK" 2>/dev/null)" = true ] || break; sleep 1; done
docker logs "$ASK" 2> /dev/null > "$OUT/asked.$MODE.txt"
awk -v t0="$T0" '
  { n++; if ($3 != "200") { bad++; if (!first) first = $1 } else { if (first && !back) back = $1 + $2 } if ($2 > slow) slow = $2 }
  END { printf "%s: %d requests · %d not answered 200 · slowest answer %.1f s · ", mode, n, bad, slow / 1000
        if (first) printf "away from the first failure for %.1f s\n", (back - first) / 1000; else print "no request failed" }' mode="$MODE" "$OUT/asked.$MODE.txt"
docker logs "$CADDY" 2>&1 | grep -c "no upstreams available" | sed 's/^/front door said "no upstreams available": /'
down
