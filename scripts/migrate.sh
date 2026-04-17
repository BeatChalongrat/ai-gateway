#!/usr/bin/env bash
# SRAN AI Gateway — Run database migrations
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [ -d .venv ]; then
  source .venv/bin/activate
fi

echo "[*] Running Alembic migrations..."
alembic upgrade head
echo "[*] Migrations complete."
echo ""
alembic current
