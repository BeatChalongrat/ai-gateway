#!/usr/bin/env bash
# AM10 gate line — AM8's four mutated files against this checkout's quality gate (am8_mutations.py),
# in the sandbox image with no network, the way the Python lane runs.
#
#   bash scripts/probes/AM10/am8_mutations.sh > scripts/probes/AM10/results/am8_mutations.tsv
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 \
  -v "$ROOT/backend:/src:ro" -w /src \
  -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  -v "$ROOT/scripts/probes/AM10:/am10:ro" \
  --entrypoint python3 "$IMAGE" /am10/am8_mutations.py
