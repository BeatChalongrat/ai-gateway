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

# FIX130 E4 — stdin -> stdout with anything secret-shaped replaced: the
# literal values of this service's secrets (DB passwords, the off-site repo
# password and keys, the Resend key, the ntfy token), credentials in URLs,
# JWTs, key-shaped and long random tokens, and SQL statements. Every alert
# message goes through it (notify.sh), and so does every line restic prints
# (offsite.sh). The Python twin is backend/app/services/alert_scrub.py.
redact() {
  perl -CSD -pe '
    BEGIN {
      @lit = sort { length($b) <=> length($a) } grep { defined && length($_) >= 4 }
             map { $ENV{$_} } qw(POSTGRES_PASSWORD WEBCHAT_POSTGRES_PASSWORD
               BACKUP_OFFSITE_PASSWORD BACKUP_OFFSITE_ACCESS_KEY_ID BACKUP_OFFSITE_SECRET_ACCESS_KEY
               RESEND_API_KEY ALERT_NTFY_TOKEN);
      # The off-site repository (and its bare endpoint URL, which restic
      # prints in network errors) is named, not shown: bucket and path stay
      # out of logs and alerts.
      @repo = sort { length($b) <=> length($a) } grep { defined && length($_) >= 4 }
              map { $ENV{$_} } qw(BACKUP_OFFSITE_REPOSITORY BACKUP_OFFSITE_REPOSITORY_URL);
    }
    for my $s (@repo) { s/\Q$s\E/<offsite repository>/g }
    for my $s (@lit) { s/\Q$s\E/[redacted]/g }
    s{(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@}{$1\[redacted\]@}g;
    s/\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]*)?/[redacted token]/g;
    s/(?i)\b(bearer\s+)(?!\[redacted)[A-Za-z0-9._~+\/\-]{8,}=*/$1\[redacted\]/g;
    s/(?i)\b(token|password|passwd|pwd|secret|api[_-]?key|access[_-]?key)(\s*[=:]\s*)(?!\[redacted)[^\s,;\x27"]{4,}/$1$2\[redacted\]/g;
    s/\b(?:sk|pk|rk|re|tk|ak|ghp|gho|ghs|glpat|xox[abprs])[-_][A-Za-z0-9_\-]{12,}/[redacted key]/g;
    s/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/[redacted key]/g;
    s/(?<![A-Za-z0-9+\/_\-])(?=[A-Za-z0-9+\/_\-]*[0-9])(?=[A-Za-z0-9+\/_\-]*[A-Za-z])(?:[A-Za-z0-9+\/_]{32,}={0,2}|[A-Za-z0-9_\-]{40,})(?![A-Za-z0-9+\/_\-])/[redacted]/g;
    s/(?:(?i:\bselect\b[^\n]*?\bfrom\b)|(?i:\binsert\s+into\b)|(?i:\bupdate\s+\S+\s+set\b)|(?i:\bdelete\s+from\b)|(?i:\b(?:alter|create|drop|truncate)\s+(?:table|index|database|schema|role|user|extension)\b)|(?i:\bcopy\s+\S+\s+(?:from|to)\b)|\b(?:SELECT|INSERT|UPDATE|DELETE|WITH|BEGIN|COMMIT|ROLLBACK|ALTER|CREATE|DROP|TRUNCATE|COPY|VACUUM|LOCK)\b\s+\S)[^\n]*/[sql redacted]/g;
  '
}
