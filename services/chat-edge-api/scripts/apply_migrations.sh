#!/usr/bin/env bash
# Apply pending chat-edge SQL migrations to the running postgres-webchat.
#
# Why this exists: docker-compose mounts the migrations dir into
# /docker-entrypoint-initdb.d/, which Postgres only runs the FIRST
# time the volume is created. Migrations added AFTER the volume is
# initialized never apply — the chat-edge process boots, the schema is
# missing the new tables/columns, and every endpoint that touches them
# 500s with no log (the HTTP middleware logs status but not the SQL
# error).
#
# Tracking: this script maintains chat._migrations(filename pk,
# applied_at). On first run against a DB that was bootstrapped by
# initdb, every file already on disk is recorded as applied — that's
# safe because initdb ran them all and they aren't idempotent. On
# subsequent runs, only files NOT in the tracking table are applied.
#
# Usage:
#   ./services/chat-edge-api/scripts/apply_migrations.sh
set -euo pipefail

CONTAINER="${CHAT_EDGE_PG_CONTAINER:-ai-gateway-postgres-webchat}"
DB_USER="${CHAT_EDGE_PG_USER:-chatedge}"
DB_NAME="${CHAT_EDGE_PG_DB:-sran_ai}"

echo "[chat-edge] applying migrations against $CONTAINER ($DB_USER@$DB_NAME)"

# All logic runs inside the container so MSYS / Git-Bash on Windows
# doesn't rewrite the absolute /docker-entrypoint-initdb.d/ paths.
docker exec -e DB_USER="$DB_USER" -e DB_NAME="$DB_NAME" "$CONTAINER" bash -c '
  set -e
  PSQL="psql -U $DB_USER -d $DB_NAME -v ON_ERROR_STOP=1 -X -q -t -A"

  # Bootstrap tracking table. Lives in the chat schema (created by 002)
  # if present, else in public — we resolve at runtime.
  if $PSQL -c "SELECT 1 FROM information_schema.schemata WHERE schema_name='"'"'chat'"'"'" | grep -q 1; then
    SCHEMA=chat
  else
    SCHEMA=public
  fi
  $PSQL -c "CREATE TABLE IF NOT EXISTS ${SCHEMA}._migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW());"

  # First-run seed: if the tracking table is empty but identity.users
  # exists (signal that initdb ran the bootstrap migrations), record
  # every on-disk file as applied so we do not re-attempt non-
  # idempotent migrations 001-005.
  count=$($PSQL -c "SELECT count(*) FROM ${SCHEMA}._migrations")
  if [ "$count" = "0" ] && $PSQL -c "SELECT 1 FROM information_schema.tables WHERE table_schema='"'"'identity'"'"' AND table_name='"'"'users'"'"'" | grep -q 1; then
    echo "[chat-edge] first run: marking already-applied migrations as recorded"
    for f in /docker-entrypoint-initdb.d/*.sql; do
      [ -e "$f" ] || continue
      $PSQL -c "INSERT INTO ${SCHEMA}._migrations(filename) VALUES ('"'"'$(basename "$f")'"'"') ON CONFLICT DO NOTHING;" >/dev/null
    done
    echo "[chat-edge] tracking table seeded; nothing else to apply"
    exit 0
  fi

  applied=0
  for f in /docker-entrypoint-initdb.d/*.sql; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    if $PSQL -c "SELECT 1 FROM ${SCHEMA}._migrations WHERE filename='"'"'$name'"'"'" | grep -q 1; then
      continue
    fi
    echo "  >> $name"
    psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$f" >/dev/null
    $PSQL -c "INSERT INTO ${SCHEMA}._migrations(filename) VALUES ('"'"'$name'"'"') ON CONFLICT DO NOTHING;" >/dev/null
    applied=$((applied+1))
  done
  echo "[chat-edge] applied $applied migration(s)"
'
