#!/bin/bash
# R11 B1: apply chat-edge SQL migrations to the MAIN-stack postgres (the
# init hook only runs on first volume init). Idempotent-ish: chat-edge
# migrations are mostly CREATE IF NOT EXISTS; failures on already-applied
# statements are reported — review output.
#
# Usage: bash scripts/apply_chat_edge_migrations_main.sh [compose-project-args...]
set -e

CHAT_DB="${CHAT_EDGE_DB:-sran_chat}"
PG_USER="${POSTGRES_USER:-postgres}"

echo "Ensuring database ${CHAT_DB} exists..."
docker compose exec -T postgres psql -U "$PG_USER" -d "${POSTGRES_DB:-ai_gateway}" \
    -c "SELECT 'CREATE DATABASE ${CHAT_DB}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${CHAT_DB}')\gexec"

for f in services/chat-edge-api/migrations/*.sql; do
    echo "Applying $f"
    docker compose exec -T postgres psql -U "$PG_USER" -d "$CHAT_DB" -v ON_ERROR_STOP=0 < "$f"
done
echo "Done."
