#!/usr/bin/env bash
# FIX129 A5 — shared helpers for the backup service scripts.
# Sourced, not executed.

# One line per step, UTC timestamp first, to stdout (= `docker logs`).
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR $*"; exit 1; }

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
UPLOADS_DIR="${UPLOADS_DIR:-/uploads}"

# Gateway DB (the base project's `postgres`).
GW_HOST="${POSTGRES_HOST:-postgres}"
GW_PORT="${POSTGRES_PORT:-5432}"
GW_DB="${POSTGRES_DB:-ai_gateway}"
GW_USER="${POSTGRES_USER:-postgres}"
GW_PASSWORD="${POSTGRES_PASSWORD:-}"
GW_TABLES=(tenants portal_users request_logs credit_transactions model_configs)

# Chat DB (the webchat file's `postgres-webchat`).
CHAT_HOST="${WEBCHAT_POSTGRES_HOST:-postgres-webchat}"
CHAT_PORT="${WEBCHAT_POSTGRES_PORT:-5432}"
CHAT_DB="${WEBCHAT_POSTGRES_DB:-sran_ai}"
CHAT_USER="${WEBCHAT_POSTGRES_USER:-chatedge}"
CHAT_PASSWORD="${WEBCHAT_POSTGRES_PASSWORD:-}"
CHAT_TABLES=(chat.conversations chat.messages_masked chat.conversation_files)

# Scratch database names the drill restores into (always dropped afterwards).
GW_DRILL_DB="${GW_DRILL_DB:-ai_gateway_drill}"
CHAT_DRILL_DB="${CHAT_DRILL_DB:-sran_ai_drill}"

# psql against one of the two sources / scratch DBs, tuples only, no
# psqlrc, stop on the first error. $1 = gw|chat, $2 = database, rest = psql args.
pg() {
  local which="$1" db="$2"; shift 2
  case "$which" in
    gw)   PGPASSWORD="$GW_PASSWORD"   psql -X -q -At -v ON_ERROR_STOP=1 -h "$GW_HOST"   -p "$GW_PORT"   -U "$GW_USER"   -d "$db" "$@" ;;
    chat) PGPASSWORD="$CHAT_PASSWORD" psql -X -q -At -v ON_ERROR_STOP=1 -h "$CHAT_HOST" -p "$CHAT_PORT" -U "$CHAT_USER" -d "$db" "$@" ;;
    *) die "pg: unknown source '$which'" ;;
  esac
}

# Row count of one table in one DB: count_rows gw|chat <db> <table>
count_rows() { pg "$1" "$2" -c "SELECT count(*) FROM $3"; }

file_bytes() { stat -c %s "$1"; }
file_sha256() { sha256sum "$1" | cut -d' ' -f1; }

# The connection parameters that are safe to print (never the passwords).
describe_sources() {
  printf 'gateway=%s:%s/%s as %s; chat=%s:%s/%s as %s' \
    "$GW_HOST" "$GW_PORT" "$GW_DB" "$GW_USER" "$CHAT_HOST" "$CHAT_PORT" "$CHAT_DB" "$CHAT_USER"
}
