#!/usr/bin/env bash
# AM18 — one piece of the engine's Python lane, by file pattern, in the stock sandbox image (as
# scripts/agent-market/python-lane.sh runs the whole suite). Exit code = the suite's.
#
#   bash scripts/probes/AM18/py.sh 'test_bank*.py'                 # the files that match
#   bash scripts/probes/AM18/py.sh 'test_am14_words.py' -k Bank    # and a unittest -k filter
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
PATTERN="${1:?a file pattern, e.g. 'test_bank*.py'}"
shift
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT/backend:/src:ro" -w /src \
  -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  --entrypoint python3 "$IMAGE" \
  -c "import sys; sys.path[0:0]=['/src','/sandbox/scripts']; import unittest; sys.argv=['unittest','discover','-s','agent_market/tests','-t','.','-p',sys.argv[1]]+sys.argv[2:]; unittest.main(module=None)" "$PATTERN" "$@"
