#!/usr/bin/env bash
# Agent Market — is an agent generic? Runs scripts/agent-market/generic_check.py inside the stock
# sandbox image (where the agents run: the self-test needs what the image holds), with the checkout
# mounted read-only and no network.
#
#   bash scripts/agent-market/generic-check.sh                  # every package
#   bash scripts/agent-market/generic-check.sh po_matching      # one
#   bash scripts/agent-market/generic-check.sh --json ...       # the whole result as JSON
#   AM_DELIVERED=<dir> bash scripts/agent-market/generic-check.sh
#       # also what a fresh install delivered: <dir>/<package>/<the files of its self-test round>
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
DELIVERED=()
ARGS=()
if [ -n "${AM_DELIVERED:-}" ]; then
  DELIVERED=(-v "$(cd "$AM_DELIVERED" && (pwd -W 2>/dev/null || pwd)):/delivered:ro")
  ARGS=(--out /delivered)
fi
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e AM_BACKEND=/src -e AM_SANDBOX_SCRIPTS=/sandbox/scripts \
  -v "$ROOT/backend:/src:ro" -w /src \
  -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  -v "$ROOT/scripts/agent-market:/tools:ro" \
  ${DELIVERED[@]+"${DELIVERED[@]}"} \
  --entrypoint python3 "$IMAGE" /tools/generic_check.py ${ARGS[@]+"${ARGS[@]}"} "$@"
