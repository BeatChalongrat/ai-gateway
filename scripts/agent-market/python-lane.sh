#!/usr/bin/env bash
# Agent Market — Python lane (engine, checks, compute, deliverables, eval set).
#
# Runs the unittest suite inside the stock sandbox image, which is where
# this code runs in production: --network none, read-only source, no
# pytest (the image does not ship it). Exit code = the suite's.
# The checkout's services/chat-edge-api/scripts is mounted over the image's
# /sandbox/scripts (AM2): the engine imports extract_text from there, and the
# lane must test the code under test, not the copy baked into the image.
#
#   bash scripts/agent-market/python-lane.sh            # whole suite
#   bash scripts/agent-market/python-lane.sh -k eval    # unittest -k filter
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
# on battery the lane does not start (scripts/ops/power.sh); RUN_ON_BATTERY=yes on the command runs it anyway
bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/ops/power.sh" need "the engine's Python lane" || exit 3
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT/backend:/src:ro" -w /src \
  -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  --entrypoint python3 "$IMAGE" \
  -c "import sys; sys.path[0:0]=['/src','/sandbox/scripts']; import unittest; sys.argv=['unittest','discover','-s','agent_market/tests','-t','.','-v']+sys.argv[1:]; unittest.main(module=None)" "$@"
