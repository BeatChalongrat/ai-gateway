#!/usr/bin/env bash
# AM13 — run one probe script inside the stock sandbox image, where the engine runs in production:
# no network, the checkout's backend read-only, the probe folder read-only, a scratch /out.
#
#   bash scripts/probes/AM13/py.sh explore_samples.py
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
OUT="$ROOT/scripts/probes/AM13/results/out"
mkdir -p "$OUT"
MSYS_NO_PATHCONV=1 docker run --rm --network none --memory 1g \
  -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 \
  -v "$ROOT/backend:/src:ro" -w /src \
  -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
  -v "$ROOT/scripts/probes/AM13:/probe:ro" -v "$OUT:/out" \
  --entrypoint python3 "$IMAGE" \
  -c "import sys, runpy; sys.path[0:0]=['/src','/sandbox/scripts']; sys.argv=sys.argv[1:]; runpy.run_path('/probe/'+sys.argv[0], run_name='__main__')" "$@"
