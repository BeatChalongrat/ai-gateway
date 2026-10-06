#!/usr/bin/env bash
# AM11b — runs scripts/probes/AM11b/echo_guard_static.py where the guard lives: the backend image, the
# checkout's backend/ at /app (read-only), the runner's source beside it, no network. It changes nothing.
#   bash scripts/probes/AM11b/echo_guard.sh
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT=$(git rev-parse --show-toplevel)
IMG=${LANE_IMAGE:-ai-gateway-backend:latest}
docker run --rm --network none -e APP_ENV=test -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 -e PYTHONPATH=/app -w /app \
  -v "$ROOT/backend:/app:ro" \
  -v "$ROOT/services/chat-edge-api:/services/chat-edge-api:ro" \
  -v "$ROOT/scripts/probes/AM11b:/probe:ro" \
  "$IMG" python /probe/echo_guard_static.py
