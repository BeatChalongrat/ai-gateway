#!/usr/bin/env bash
# Agent Market — Python lane (engine, checks, compute, deliverables, eval set).
#
# Runs the unittest suite inside the stock sandbox image, which is where
# this code runs in production: --network none, read-only source, no
# pytest (the image does not ship it). Exit code = the suite's.
#
#   bash scripts/agent-market/python-lane.sh            # whole suite
#   bash scripts/agent-market/python-lane.sh -k eval    # unittest -k filter
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT/backend:/src:ro" -w /src \
  --entrypoint python3 "$IMAGE" \
  -c "import sys; sys.path[0:0]=['/src','/sandbox/scripts']; import unittest; sys.argv=['unittest','discover','-s','agent_market/tests','-t','.','-v']+sys.argv[1:]; unittest.main(module=None)" "$@"
