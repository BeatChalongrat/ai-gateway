#!/usr/bin/env bash
# FIX131 — the backend test suite in a THROWAWAY container, never the live one.
#
#   scripts/backend-test-lane.sh                     # the CI unit lane (-m "not integration")
#   scripts/backend-test-lane.sh tests/test_x.py -q  # any pytest arguments
#
# * the checkout's backend/ is mounted READ-ONLY at /app, with the cross-tree
#   paths some tests read (docs, examples, chat-edge Go source, desktop
#   sources, compose files, infra, the DLP parity fixtures, and the privacy
#   sidecar's word lists and source — fourteen tests that hold the Python
#   detectors' lists to the sidecar's were skipped in every lane for want
#   of that mount);
# * --network none: nothing in the lane can reach a database, Redis, a
#   provider or an alert channel (FIX90: a test run inside the live container
#   once migrated the live database; FIX130: the lane paged the owner);
# * the environment is APP_ENV=test and nothing else;
# * the image only supplies the Python packages ($LANE_IMAGE, default
#   ai-gateway-backend:latest); the code under test is the checkout's;
# * pytest.main + os._exit: a plain `pytest` can hang at interpreter exit in
#   this image (a non-daemon thread), see the pytest-hang finding.
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT=$(git rev-parse --show-toplevel)
IMG=${LANE_IMAGE:-ai-gateway-backend:latest}
[ $# -gt 0 ] || set -- -q -p no:cacheprovider -m "not integration" -rf
# app/main.py creates backend/uploads at import; the mount is read-only.
mkdir -p "$ROOT/backend/uploads/avatars"
docker run --rm --network none -e APP_ENV=test -e PYTHONDONTWRITEBYTECODE=1 -w /app \
  -v "$ROOT/backend:/app:ro" \
  -v "$ROOT/docs:/docs:ro" -v "$ROOT/examples:/examples:ro" \
  -v "$ROOT/dlp-sidecar/data:/dlp-sidecar/data:ro" -v "$ROOT/dlp-sidecar/src:/dlp-sidecar/src:ro" \
  -v "$ROOT/services/chat-edge-api:/services/chat-edge-api:ro" \
  -v "$ROOT/SranDesktop/src:/SranDesktop/src:ro" -v "$ROOT/apps/desktop/src:/apps/desktop/src:ro" \
  -v "$ROOT/docker-compose.yml:/docker-compose.yml:ro" \
  -v "$ROOT/docker-compose.prod.yml:/docker-compose.prod.yml:ro" \
  -v "$ROOT/docker-compose.webchat.yml:/docker-compose.webchat.yml:ro" \
  -v "$ROOT/docker-compose.agent-market.yml:/docker-compose.agent-market.yml:ro" \
  -v "$ROOT/scripts/agent-market/test-web.yml:/scripts/agent-market/test-web.yml:ro" \
  -v "$ROOT/infra:/infra:ro" \
  -v "$ROOT/dlp-sidecar/tests/parity/fixtures:/work/dlp-sidecar/tests/parity/fixtures:ro" \
  "$IMG" python -c "import os,sys,pytest; rc=pytest.main(sys.argv[1:]); sys.stdout.flush(); os._exit(int(rc))" "$@"
