#!/usr/bin/env bash
# One-shot cleanup of test pollution from request_logs.
#
# REQUIRES interactive confirmation — never run unattended. Removes rows
# whose tenant_id is not the operator's intended tenant. Used once during
# the May 2026 test/dev DB separation work; should not be needed again
# now that pytest is properly isolated from the live gateway DB.
#
# Usage:
#   export BEAT_TENANT_ID=demo-tenant-000
#   ./scripts/cleanup-test-pollution.sh

set -euo pipefail

BEAT_TENANT_ID="${BEAT_TENANT_ID:?Set BEAT_TENANT_ID to the tenant_id you want to KEEP (e.g. demo-tenant-000)}"

echo "About to DELETE all request_logs rows where tenant_id != '$BEAT_TENANT_ID'"
echo
echo "Current breakdown:"
# ``< /dev/null`` keeps ``docker compose exec`` from consuming the
# script's stdin — without it, a subsequent ``read`` prompt below
# silently gets EOF and the script aborts.
docker compose exec -T postgres psql -U postgres -d ai_gateway -c "
SELECT tenant_id, COUNT(*) FROM request_logs GROUP BY tenant_id ORDER BY 2 DESC;
" < /dev/null
echo
read -p "Type 'DELETE' to confirm: " confirm
[[ "$confirm" == "DELETE" ]] || { echo "Aborted."; exit 1; }

docker compose exec -T postgres psql -U postgres -d ai_gateway -v "ON_ERROR_STOP=1" \
  -v keeper="$BEAT_TENANT_ID" -c "
BEGIN;
DELETE FROM request_logs WHERE tenant_id != :'keeper';
SELECT 'After cleanup:' AS info;
SELECT tenant_id, COUNT(*) FROM request_logs GROUP BY tenant_id;
COMMIT;
" < /dev/null
