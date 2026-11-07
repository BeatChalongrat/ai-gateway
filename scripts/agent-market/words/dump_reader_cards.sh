#!/usr/bin/env bash
# AM16 — writes services/chat-edge-api/internal/agentmarket/am16_reader_table_test.go: the cards of a scanned
# invoice whose page a model outside the organisation read, in Thai and in English
# (dump_reader_cards.py, run in the sandbox image, which is where the packages run).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 -e AM_SRC=/src \
  -v "$ROOT/backend:/src:ro" -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  -v "$ROOT/scripts/agent-market/words:/probe:ro" \
  -v "$ROOT/services/chat-edge-api/internal/agentmarket:/go" \
  --entrypoint python3 "$IMAGE" /probe/dump_reader_cards.py /go/am16_reader_table_test.go
echo "wrote services/chat-edge-api/internal/agentmarket/am16_reader_table_test.go"
