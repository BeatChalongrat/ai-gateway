#!/usr/bin/env bash
# RETIRED (FIX129 A5). This script dumped ONLY the gateway database, as
# plain SQL, with no restore proof and nothing scheduling it. The backup is
# now the `backup` compose service (infra/backup, docs/ops/backups.md): both
# databases as pg_dump -Fc, the uploads volume, a manifest, a restore drill
# on every run, retention, and alerts — scheduled daily inside the stack.
#
# This wrapper runs one backup now, the same way the docs say to:
#   docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now
# Env vars of the old script (BACKUP_ROOT, RETENTION_DAYS, COMPOSE_FILES) are
# ignored; the service reads BACKUP_DIR / BACKUP_KEEP_DAYS from .env.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
echo "[backup-db] this script is retired; running the compose backup service once (see docs/ops/backups.md)" >&2
exec docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now
