#!/usr/bin/env bash
# AM12 — writes services/chat-edge-api/internal/agentmarket/am12_packages_test.go's table: what the
# procurement package hands to a thread, in both languages (dump_procurement_texts.py, run in the
# sandbox image, which is where the package runs).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 \
  -v "$ROOT/backend:/src:ro" -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  -v "$ROOT/scripts/agent-market/words:/probe:ro" -v "$ROOT/services/chat-edge-api/internal/agentmarket:/go" \
  --entrypoint python3 "$IMAGE" /probe/dump_procurement_texts.py /go/am12_packages_table_test.go
echo "wrote services/chat-edge-api/internal/agentmarket/am12_packages_table_test.go"
