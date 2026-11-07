#!/usr/bin/env bash
# Build the template database integration tests clone from.
#
# Each test that calls internal/testdb.Fresh gets its own database,
# copied from this template, and drops it when the test ends. That is
# why those tests need no cleanup code: the whole database goes away.
#
# The template is built by replaying the migration files into an empty
# database, so it always matches the schema on disk. Re-running this
# script rebuilds it from scratch; it is safe to run any time no test
# is currently running.
#
# Usage:
#   services/chat-edge-api/scripts/make_test_template.sh
#
# Env:
#   PG_CONTAINER   docker container running Postgres (default: ai-gateway-postgres-webchat)
#   PG_USER        role to connect as               (default: chatedge)
#   TEMPLATE_DB    database to build                (default: chat_test_template)
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-ai-gateway-postgres-webchat}"
PG_USER="${PG_USER:-chatedge}"
TEMPLATE_DB="${TEMPLATE_DB:-chat_test_template}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
migrations="$here/../migrations"

if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  echo "[test-template] $PG_CONTAINER is not running" >&2
  exit 1
fi

echo "[test-template] rebuilding $TEMPLATE_DB"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -q \
  -c "DROP DATABASE IF EXISTS $TEMPLATE_DB WITH (FORCE);" \
  -c "CREATE DATABASE $TEMPLATE_DB;"

# NUL-delimited so a repository path containing spaces cannot split the
# file list (this checkout lives under a directory with a space in it).
applied=0
while IFS= read -r -d '' f; do
  if docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEMPLATE_DB" \
       -q -v ON_ERROR_STOP=1 < "$f" >/dev/null 2>&1; then
    applied=$((applied + 1))
  else
    echo "[test-template] FAILED on $(basename "$f")" >&2
    exit 1
  fi
done < <(find "$migrations" -maxdepth 1 -name '*.sql' -print0 | sort -z)

tables=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEMPLATE_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='chat';")

echo "[test-template] applied $applied migration(s); chat schema has $tables table(s)"
