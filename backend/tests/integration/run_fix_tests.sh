#!/usr/bin/env bash
# Integration test runner for the 6 DLP + routing fixes.
#
# Bounces the backend container with PROVIDER_BASE_URL_OVERRIDES set so
# adapter calls land at the in-cluster mock-provider (otherwise the
# placeholder sk-... keys would 401 against real upstreams and every
# test would skip). Restores the original backend settings on exit.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT_DIR"

export AI_GATEWAY_BASE_URL="${AI_GATEWAY_BASE_URL:-http://localhost:8080}"
export AI_GATEWAY_PG_DSN="${AI_GATEWAY_PG_DSN:-postgresql://postgres:postgres@localhost:5432/ai_gateway}"

# The adapter base_url constants include a trailing ``/v1``; the
# override must follow suit so the adapter's ``client.post("/chat/...")``
# resolves to the right path on the mock server.
OVERRIDES='{"openai":"http://mock-provider:9090/v1","anthropic":"http://mock-provider:9090/v1","qwen":"http://mock-provider:9090/v1"}'

echo "=== AI Gateway Fix Integration Tests ==="
echo "Backend:  ${AI_GATEWAY_BASE_URL}"
echo "DB:       ${AI_GATEWAY_PG_DSN}"
echo "Provider override: ${OVERRIDES}"

# Stop the backend and bring it back with the override env. We re-use
# the existing compose service definition so volumes / networking stay
# intact.
echo ""
echo "--- Bouncing backend with provider override ---"
docker compose stop backend >/dev/null
PROVIDER_BASE_URL_OVERRIDES="$OVERRIDES" docker compose up -d backend >/dev/null

# Wait for /health to flip back to 200.
for i in $(seq 1 30); do
  if curl -fsS "${AI_GATEWAY_BASE_URL}/health" >/dev/null 2>&1; then
    echo "Backend healthy after ${i}s."
    break
  fi
  sleep 1
done

REPORT_PATH="$ROOT_DIR/backend/tests/integration/fix_test_report.txt"
echo ""
echo "--- Running pytest (inside backend container) ---"
# Inside the container, the backend URL and the Postgres DSN both
# resolve via the compose network (service names, not localhost).
set +e
docker compose exec -T \
  -e AI_GATEWAY_BASE_URL=http://localhost:8080 \
  -e AI_GATEWAY_PG_DSN=postgresql://postgres:postgres@postgres:5432/ai_gateway \
  backend pytest tests/integration/test_fixes_live.py \
  -v --tb=short --no-header -rN \
  2>&1 | tee "$REPORT_PATH"
PYTEST_RC=$?
set -e

echo ""
echo "=== Summary ==="
grep -E "PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED|warnings" "$REPORT_PATH" | tail -30

# Restore the backend to its original env (no override).
echo ""
echo "--- Restoring backend env ---"
docker compose stop backend >/dev/null
docker compose up -d backend >/dev/null

exit $PYTEST_RC
