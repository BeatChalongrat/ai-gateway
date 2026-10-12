#!/usr/bin/env bash
# FIX131 — deploy the backend image (and ops-watch, same image) with in-flight
# turns drained and no gap for new requests. See docs/ops/deploy-backend.md.
#
#   scripts/deploy-backend.sh [options]
#
#   scripts/deploy-backend.sh                        # build HEAD, deploy start-first
#   scripts/deploy-backend.sh --rev 1a2b3c4          # build that commit
#   scripts/deploy-backend.sh --image ai-gateway-backend:rollback-380b3f4   # roll back
#   scripts/deploy-backend.sh --stop-first           # no overlap (see below)
#
# Start-first (default): a BRIDGE container of the new image joins the compose
# network under the alias `backend` (docker compose run --use-aliases, no host
# ports) and must turn healthy; then the service container is recreated —
# the old one drains its in-flight turns (gunicorn graceful_timeout, Docker
# stop_grace_period) while new connections, refused by the draining
# container, go to the bridge (Go/Node/Python clients try the next address of
# `backend`); when the new service container is healthy the bridge drains and
# is removed. Both containers elect ONE leader for the singleton jobs
# (Postgres advisory lock), so the overlap runs nothing twice.
#
# Stop-first (--stop-first): plain `up -d --no-deps` — new requests fail while
# the old container drains and the new one boots (= drain + ~15 s). Use it when
# the new image's migrations are NOT backward compatible with the running code:
# in start-first the bridge migrates the schema while the old version still
# serves. The script refuses start-first when a migration is pending unless
# you pass --allow-migrations (after checking it is expand-only).
#
# Options:
#   --rev <rev>            commit to build (default HEAD); ignored with --image
#   --image <ref>          deploy an existing image instead of building
#   --stop-first           no bridge (see above)
#   --allow-migrations     start-first even though the new image migrates
#   --probe-url <url>      polled every 0.25 s during the deploy to measure the
#                          gap (default http://127.0.0.1:28080/live: the Go
#                          gateway -> backend path through the gateway's plain
#                          loopback door; :8080 is the https front door since
#                          FIX133 and answers 308 to plain http); "" = no probe
#   -p <project>           compose project (default ai-gateway)
#   -f <file>              compose file (repeatable; default: compose's own
#                          default = docker-compose.yml + docker-compose.override.yml)
#   --no-ops-watch         do not recreate ops-watch
#   --dry-run              print what would run
#
# Maintenance window: around the deploy the script opens the alert maintenance
# window (backend/app/ops/maintenance.py, run inside ops-watch) so a planned
# restart never pages at top priority. DEPLOY_MAINTENANCE_CMD overrides the
# command (it receives `start <minutes> "<reason>"` / `stop`); set it to "" to skip.
#
# On battery the deploy is refused and nothing is touched (scripts/ops/power.sh);
# RUN_ON_BATTERY=yes on the command runs it anyway. A --dry-run is not refused.
#
# Never touches the checkout's files; builds from `git archive` (build-images.sh).
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

REV_IN=HEAD
IMAGE_IN=""
MODE=start-first
ALLOW_MIGRATIONS=0
PROBE_URL="http://127.0.0.1:28080/live"  # FIX134: not :8080 (https front door, 308)
PROJECT=ai-gateway
FILES=()
OPS_WATCH=1
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV_IN="$2"; shift 2 ;;
    --image) IMAGE_IN="$2"; shift 2 ;;
    --stop-first) MODE=stop-first; shift ;;
    --allow-migrations) ALLOW_MIGRATIONS=1; shift ;;
    --probe-url) PROBE_URL="$2"; shift 2 ;;
    -p) PROJECT="$2"; shift 2 ;;
    -f) FILES+=(-f "$2"); shift 2 ;;
    --no-ops-watch) OPS_WATCH=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

export MSYS_NO_PATHCONV=1
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
# Nothing heavy starts on battery (scripts/ops/power.sh): a deploy the battery cuts short leaves live
# between two images. Refused here, before an image is built or a container is touched.
[ "$DRY" = 1 ] || bash scripts/ops/power.sh need "the backend deploy" || exit 3
DC=(docker compose -p "$PROJECT" "${FILES[@]}")
BRIDGE="${PROJECT}-backend-bridge"
GRACE=${DEPLOY_STOP_GRACE:-200}
log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
run() { if [ "$DRY" = 1 ]; then echo "+ $*" >&2; else "$@"; fi; }

# --- refuse a dev shape --------------------------------------------------------
CONFIG_JSON=$("${DC[@]}" config --format json)
if printf '%s' "$CONFIG_JSON" | grep -q -- '--reload'; then
  echo "REFUSED: the compose shape runs the backend with --reload (docker-compose.dev.yml in COMPOSE_FILE / -f?)." >&2
  echo "Deploy the image shape: no dev file." >&2
  exit 1
fi
# The backend service's OWN image repository (`config --images backend` also
# lists its dependencies' images). No `image:` key = compose's default name,
# <project>-backend (live: ai-gateway-backend).
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import json' >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "python3 (or python) is required" >&2; exit 1; }
REPO=$(printf '%s' "$CONFIG_JSON" | "$PY" -c "import json,sys; d=json.load(sys.stdin); print((d['services']['backend'].get('image') or '').rsplit(':',1)[0] if ':' in (d['services']['backend'].get('image') or '').split('/')[-1] else d['services']['backend'].get('image') or '')")
REPO=${REPO:-${PROJECT}-backend}
case "$REPO" in
  *backend*) ;;
  *) echo "REFUSED: backend image repository resolved to '$REPO' — not a backend image" >&2; exit 1 ;;
esac

# --- the image to deploy -------------------------------------------------------
if [ -n "$IMAGE_IN" ]; then
  NEW="$IMAGE_IN"
  docker image inspect "$NEW" >/dev/null
else
  SHA=$(git rev-parse --short "${REV_IN}^{commit}")
  NEW="$REPO:$SHA"
  if [ "$REPO" = ai-gateway-backend ]; then
    log "building $NEW from git archive $SHA"
    run bash scripts/build-images.sh --rev "$SHA" backend
  else
    docker image inspect "$NEW" >/dev/null || { echo "build $NEW first (repo $REPO is not what build-images.sh names)" >&2; exit 1; }
  fi
fi
NEW_ID=$(docker image inspect -f '{{.Id}}' "$NEW" 2>/dev/null || echo "?")
NEW_REV=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$NEW" 2>/dev/null || echo "?")

# --- what runs now: keep it as the rollback tag ----------------------------------
OLD_CID=$("${DC[@]}" ps -q backend 2>/dev/null | head -1 || true)
ROLLBACK=""
if [ -n "$OLD_CID" ]; then
  OLD_IMG=$(docker inspect -f '{{.Image}}' "$OLD_CID")
  OLD_REV=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$OLD_IMG" 2>/dev/null || true)
  ROLLBACK="$REPO:rollback-${OLD_REV:-$(date -u +%Y%m%d%H%M%S)}"
  if [ "$OLD_IMG" = "$NEW_ID" ]; then
    log "the running container already runs $NEW ($NEW_REV); recreating anyway"
  fi
  run docker tag "$OLD_IMG" "$ROLLBACK"
  log "rollback image: $ROLLBACK"
fi

# --- migrations under a running old version? --------------------------------------
if [ "$MODE" = start-first ] && [ -n "$OLD_CID" ] && [ "$ALLOW_MIGRATIONS" = 0 ]; then
  NEW_HEAD=$(docker run --rm --network none "$NEW" python -m alembic heads 2>/dev/null | awk 'NR==1{print $1}')
  CUR=$(docker exec "$OLD_CID" python -m alembic current 2>/dev/null | awk '/^[0-9a-z_]+( |$)/{print $1}' | tail -1)
  if [ -n "$NEW_HEAD" ] && [ -n "$CUR" ] && [ "$NEW_HEAD" != "$CUR" ]; then
    echo "REFUSED: the new image migrates the schema ($CUR -> $NEW_HEAD) while the old version would still serve." >&2
    echo "If the migration is expand-only (old code keeps working on the new schema), re-run with --allow-migrations;" >&2
    echo "otherwise use --stop-first." >&2
    exit 1
  fi
fi

# --- maintenance window (OPS lane hook) -------------------------------------------
# maint begin <reason> <minutes> | maint end <reason> -> the alert maintenance CLI
# (backend/app/ops/maintenance.py: `start <minutes> "<reason>"` / `stop`). By default it
# runs inside this project's ops-watch (same image and Redis as the pager);
# DEPLOY_MAINTENANCE_CMD overrides the command, DEPLOY_MAINTENANCE_CMD="" turns it off.
maint() {
  local -a cmd
  if [ "${DEPLOY_MAINTENANCE_CMD+set}" = set ]; then
    [ -n "$DEPLOY_MAINTENANCE_CMD" ] || return 0
    # shellcheck disable=SC2206
    cmd=($DEPLOY_MAINTENANCE_CMD)
  else
    cmd=("${DC[@]}" exec -T ops-watch python -m app.ops.maintenance)
  fi
  case "$1" in
    begin) run "${cmd[@]}" start "$3" "$2" ;;
    end) run "${cmd[@]}" stop ;;
  esac || log "maintenance hook '$1' failed (continuing)"
}

# --- probe: how long did the gateway -> backend path fail? ------------------------
PROBE_LOG=""
PROBE_PID=""
start_probe() {
  [ -n "$PROBE_URL" ] && [ "$DRY" = 0 ] || return 0
  PROBE_LOG=$(mktemp)
  (
    while :; do
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$PROBE_URL" || true)
      printf '%s %s\n' "$(date +%s%3N)" "${code:-000}" >> "$PROBE_LOG"
      sleep 0.25
    done
  ) &
  PROBE_PID=$!
}
stop_probe() {
  [ -n "$PROBE_PID" ] || return 0
  kill "$PROBE_PID" 2>/dev/null || true
  wait "$PROBE_PID" 2>/dev/null || true
  PROBE_PID=""
  awk '
    { t=$1; ok=($2 ~ /^2/); n++
      if (!ok) { bad++; if (start=="") start=t; last_bad=t }
      else if (start!="") { gap=t-start; if (gap>maxgap) maxgap=gap; start="" } }
    END { if (start!="") { gap=last_bad-start; if (gap>maxgap) maxgap=gap }
          printf "probe: %d requests, %d failed, longest failure window %.2f s\n", n, bad, maxgap/1000 }
  ' "$PROBE_LOG"
  rm -f "$PROBE_LOG"
}
cleanup() { stop_probe >/dev/null 2>&1 || true; }
trap cleanup EXIT

wait_healthy() {  # <container> <timeout s>
  local c="$1" t="$2" s=""
  for _ in $(seq 1 "$t"); do
    s=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || echo gone)
    case "$s" in healthy) return 0 ;; unhealthy|exited|dead|gone) break ;; esac
    sleep 1
  done
  log "$c is $s"
  return 1
}

# --- deploy ----------------------------------------------------------------------
log "deploying $NEW (revision $NEW_REV) to project $PROJECT, $MODE"
maint begin "backend deploy $NEW_REV" 10
start_probe
run docker tag "$NEW" "$REPO:latest"

if [ "$MODE" = start-first ] && [ -n "$OLD_CID" ]; then
  run docker rm -f "$BRIDGE" >/dev/null 2>&1 || true
  log "bridge: starting $BRIDGE from $NEW (alias backend, no host ports)"
  run "${DC[@]}" run -d --no-deps --use-aliases --name "$BRIDGE" backend >/dev/null
  if [ "$DRY" = 0 ] && ! wait_healthy "$BRIDGE" 180; then
    log "bridge never became healthy — aborting; the old container keeps serving"
    docker logs --tail 40 "$BRIDGE" 2>&1 | sed 's/^/  bridge| /' || true
    docker rm -f "$BRIDGE" >/dev/null 2>&1 || true
    [ -n "$ROLLBACK" ] && docker tag "$ROLLBACK" "$REPO:latest"
    maint end "backend deploy $NEW_REV"
    stop_probe
    exit 1
  fi
  log "bridge healthy"
fi

log "recreating backend (the old container drains for up to ${GRACE}s)"
run "${DC[@]}" up -d --no-deps --no-build --force-recreate --timeout "$GRACE" --wait --wait-timeout 300 backend

if [ "$MODE" = start-first ] && [ -n "$OLD_CID" ]; then
  log "bridge: draining and removing"
  run docker stop -t "$GRACE" "$BRIDGE" >/dev/null
  run docker rm "$BRIDGE" >/dev/null
fi

if [ "$OPS_WATCH" = 1 ]; then
  log "recreating ops-watch"
  run "${DC[@]}" up -d --no-deps --no-build ops-watch
fi

stop_probe
maint end "backend deploy $NEW_REV"
NOW_CID=$("${DC[@]}" ps -q backend | head -1)
log "done: backend $(docker inspect -f '{{.Name}} {{.State.Health.Status}}' "$NOW_CID" 2>/dev/null) revision $(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$NOW_CID" 2>/dev/null)"
[ -n "$ROLLBACK" ] && log "roll back with: scripts/deploy-backend.sh --image $ROLLBACK${FILES:+ ${FILES[*]}} -p $PROJECT"
exit 0
