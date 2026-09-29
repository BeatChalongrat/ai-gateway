#!/usr/bin/env bash
# AM7 — the Go lane on the host: agentmarket unit tests (+ vet), against the
# test database server only (internal/testdb: every test gets a fresh
# database cloned from chat_test_template and drops it; a DSN that names a
# live database is refused). The DSN comes from .env without being printed;
# the live Redis URL some older tests read is unset.
#
#   bash scripts/probes/AM6/go_test.sh [go test args…]   (default: the two agentmarket packages)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHAT_EDGE_TEST_DSN="$(grep '^CHAT_EDGE_TEST_DSN=' "$ROOT/.env" | cut -d= -f2- | tr -d '\r')"
export CHAT_EDGE_TEST_DSN
unset FIX41_BENCH_REDIS_URL
cd "$ROOT/services/chat-edge-api"
if [ "$#" -eq 0 ]; then
  set -- ./internal/agentmarket/... -count=1
fi
go test "$@"
