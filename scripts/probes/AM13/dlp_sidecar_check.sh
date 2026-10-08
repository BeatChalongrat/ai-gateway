#!/usr/bin/env bash
# AM13 — the privacy sidecar under test, alone: one sidecar container on a network of its own (no route
# out), and a throwaway backend container beside it running the checkout's Python against it. No test
# stack, no gateway, no database: about half a gigabyte, so it runs on a host that cannot hold a gate.
# The Thai name-recognition layer is off in the Python engine here: it is a model, the sidecar has no port
# of it, and the parity tests compare the two engines' own detectors.
#
#   bash scripts/probes/AM13/dlp_sidecar_check.sh <sidecar image tag> spans     the sentences, over gRPC
#   bash scripts/probes/AM13/dlp_sidecar_check.sh <sidecar image tag> tests     the eval gate (Rust required),
#                                                                               the live parity tests
#   bash scripts/probes/AM13/dlp_sidecar_check.sh - down                        remove what it started
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
TAG="${1:?the dlp-sidecar image tag}"; WHAT="${2:-spans}"
NET=am13-dlp; SC=am13-dlp-sidecar
down() { docker rm -f "$SC" > /dev/null 2>&1; docker network rm "$NET" > /dev/null 2>&1; true; }
[ "$WHAT" = down ] && { down; echo "removed"; exit 0; }
down
docker network create --internal "$NET" > /dev/null
MSYS_NO_PATHCONV=1 docker run -d --name "$SC" --network "$NET" --network-alias dlp-sidecar --memory 256m --oom-score-adj 500 \
  "ai-gateway-dlp-sidecar:$TAG" > /dev/null
sleep 3
echo "sidecar under test: ai-gateway-dlp-sidecar:$TAG revision $(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$SC") · $(docker inspect -f '{{.State.Status}}' "$SC")"
run() {
  MSYS_NO_PATHCONV=1 docker run --rm --network "$NET" --memory 1500m --oom-score-adj 500 \
    -e APP_ENV=test -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONIOENCODING=utf-8 -e PYTHONPATH=/app \
    -e DLP_ENGINE=rust -e DLP_GRPC_ADDRESS=dlp-sidecar:50051 -e DLP_EVAL_REQUIRE_RUST=1 -w /app \
    -e DLP_THAI_NER_ENABLED=false \
    -v "$ROOT/backend:/app:ro" -v "$ROOT/scripts/probes/AM13:/probe:ro" \
    -v "$ROOT/dlp-sidecar/tests/parity/fixtures:/work/dlp-sidecar/tests/parity/fixtures:ro" -v "$ROOT/dlp-sidecar:/dlp-sidecar:ro" \
    ai-gateway-backend:latest "$@"
}
case "$WHAT" in
  spans) run python /probe/dlp_rust_spans.py; rc=$? ;;
  matrix) run python /probe/dlp_lead_matrix.py; rc=$? ;;   # which lead-ins the engine hears, two kinds of address
  tests) run python -c "import os,sys,pytest; rc=pytest.main(sys.argv[1:]); sys.stdout.flush(); os._exit(int(rc))" \
           tests/test_dlp_eval_gate.py tests/test_dlp_parity.py tests/test_dlp_parity_batch1.py tests/test_dlp_parity_batch2.py \
           tests/test_dlp_parity_batch3.py tests/test_dlp_parity_batch4.py tests/test_rust_dlp_client.py -q -p no:cacheprovider -rs; rc=$? ;;
  *) echo "spans | matrix | tests | down"; rc=2 ;;
esac
down
exit $rc
