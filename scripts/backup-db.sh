#!/usr/bin/env bash
# Postgres backup for the AI Gateway prod stack.
#
# Dumps the running `postgres` container's database to
# ./backups/YYYYMMDD_HHMMSS/gateway.sql.gz, then prunes backup
# directories older than ${RETENTION_DAYS:-7} days.
#
# Cron example (daily at 03:15):
#   15 3 * * * cd /opt/ai-gateway && ./scripts/backup-db.sh >> /var/log/ai-gateway-backup.log 2>&1
#
# Env vars:
#   BACKUP_ROOT      Directory holding timestamped backups (default ./backups)
#   RETENTION_DAYS   Days to keep before pruning              (default 7)
#   COMPOSE_FILES    Compose -f args used to find the stack   (default base only)
#
# Reads POSTGRES_USER / POSTGRES_DB from the environment of the running
# postgres container so it can't drift from what compose started with.

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
COMPOSE_FILES="${COMPOSE_FILES:-}"

timestamp="$(date +%Y%m%d_%H%M%S)"
target_dir="${BACKUP_ROOT}/${timestamp}"
target_file="${target_dir}/gateway.sql.gz"

mkdir -p "${target_dir}"

# Pull credentials from the running container so a stale .env on the
# operator's shell can't silently dump the wrong DB.
pg_user="$(docker compose ${COMPOSE_FILES} exec -T postgres printenv POSTGRES_USER | tr -d '\r')"
pg_db="$(docker compose ${COMPOSE_FILES} exec -T postgres printenv POSTGRES_DB | tr -d '\r')"

echo "[backup-db] $(date -Iseconds) — dumping ${pg_db} as ${pg_user} -> ${target_file}"

# pg_dump runs inside the container; gzip on the host so we don't bloat
# the container image with extra tooling. -T disables TTY allocation
# (required when invoked from cron with no controlling terminal).
docker compose ${COMPOSE_FILES} exec -T postgres \
  pg_dump -U "${pg_user}" -d "${pg_db}" --no-owner --no-privileges \
  | gzip -9 > "${target_file}"

size_bytes="$(stat -c%s "${target_file}" 2>/dev/null || stat -f%z "${target_file}")"
echo "[backup-db] $(date -Iseconds) — wrote ${size_bytes} bytes"

# Prune older directories. -mindepth 1 keeps the root; +N matches
# files older than N*24h.
if [ -d "${BACKUP_ROOT}" ]; then
  pruned="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -print -exec rm -rf {} +)"
  if [ -n "${pruned}" ]; then
    echo "[backup-db] $(date -Iseconds) — pruned:"
    echo "${pruned}" | sed 's/^/  /'
  fi
fi

echo "[backup-db] $(date -Iseconds) — done"
