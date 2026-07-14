#!/bin/bash
# R11 B1: first-init hook for the main postgres volume.
# Creates the chat-edge database and applies its SQL migrations (mounted at
# /chat-edge-migrations). Runs ONLY on first volume init — for upgrades on an
# existing volume use scripts/apply_chat_edge_migrations_main.sh.
set -e

CHAT_DB="${CHAT_EDGE_DB:-sran_chat}"

echo "[init-chat-edge] creating database ${CHAT_DB} (if missing)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE ${CHAT_DB}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${CHAT_DB}')\gexec
    GRANT ALL PRIVILEGES ON DATABASE ${CHAT_DB} TO $POSTGRES_USER;
EOSQL

if [ -d /chat-edge-migrations ]; then
    echo "[init-chat-edge] applying chat-edge migrations"
    for f in /chat-edge-migrations/*.sql; do
        echo "[init-chat-edge]   $f"
        psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$CHAT_DB" -f "$f"
    done
    echo "[init-chat-edge] done"
else
    echo "[init-chat-edge] /chat-edge-migrations not mounted — skipping (apply manually)"
fi
