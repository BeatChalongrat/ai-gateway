#!/usr/bin/env bash
# AM14 — writes services/chat-edge-api/internal/agentmarket/am14_packages_table_test.go: what the packages
# whose sentences are in their manifests hand to a thread when the run is in English
# (dump_english_texts.py, run in the sandbox image, which is where the packages run).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 \
  -e AM6_PROBE=/probe6 -e AM_SRC=/src \
  -v "$ROOT/backend:/src:ro" -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  -v "$ROOT/scripts/probes/AM14:/probe:ro" -v "$ROOT/scripts/probes/AM6:/probe6:ro" \
  -v "$ROOT/services/chat-edge-api/internal/agentmarket:/go" \
  --entrypoint python3 "$IMAGE" /probe/dump_english_texts.py /go/am14_packages_table_test.go
echo "wrote services/chat-edge-api/internal/agentmarket/am14_packages_table_test.go"
