#!/usr/bin/env bash
# FIX129 C — TEST STACK ONLY: make the ops watcher fire every finding once.
#
# Tick 1: gateway + backend stopped, disk/memory thresholds forced to 1 % →
#   "gateway has exited", "backend has exited", "backend does not answer
#   /ready", "disk … full", "memory … used" are announced (test ntfy topic).
# Tick 2: both started again, normal thresholds → "recovered: …" for each.
# The watcher runs as a throwaway container from the backend image with the
# worktree's code, the docker socket read-only, and the test stack's network.
# Refuses anything but project sran-test.
set -uo pipefail
export MSYS_NO_PATHCONV=1
OUT="${1:?results dir}"; ENVF="${2:?scratch env file with OPS_WATCH_PG_DSNS + ALERT_NTFY_URL}"
W="C:/Users/Beat Beat/Desktop/ai-gateway-fix129"
mkdir -p "$OUT"
for c in sran-test-gateway-1 sran-test-backend-1; do
  p=$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.project"}}')
  [ "$p" = "sran-test" ] || { echo "REFUSED: $c is in project '$p'"; exit 2; }
done
tick() { # $1 = label, rest = extra -e args
  local label="$1"; shift
  docker run --rm --network sran-test_default --env-file "$ENVF" -e PYTHONDONTWRITEBYTECODE=1 \
    -e OPS_WATCH_PROJECT=sran-test -e OPS_WATCH_BACKEND_URL=http://backend:8080 -e OPS_WATCH_COOLDOWN=600 \
    -e OPS_WATCH_STATE_FILE=/state/ops_watch_state.json "$@" \
    -v "$W/backend:/app:ro" -v /var/run/docker.sock:/var/run/docker.sock:ro -v /var/lib/docker:/host/docker:ro \
    -v "$W/scripts/probes/FIX129/results/ops_watch_state:/state" \
    -w /app ai-gateway-backend python -m app.ops.watch once 2>&1 | grep -E '^\{' | tee "$OUT/$label.jsonl"
}
echo "== tick 0: everything up, normal thresholds (expect no findings)"
tick tick0
echo "== stop gateway + backend"
docker stop sran-test-gateway-1 sran-test-backend-1 >/dev/null
sleep 3
echo "== tick 1: forced disk/memory thresholds"
tick tick1 -e OPS_WATCH_DISK_PCT=1 -e OPS_WATCH_MEM_PCT=1
echo "== start gateway + backend"
docker start sran-test-gateway-1 sran-test-backend-1 >/dev/null
for i in $(seq 1 40); do
  s=$(docker inspect sran-test-backend-1 --format '{{.State.Health.Status}}' 2>/dev/null); [ "$s" = "healthy" ] && break; sleep 3
done
echo "backend health: $s"
echo "== tick 2: normal thresholds (expect recovered notes)"
tick tick2
