#!/usr/bin/env bash
# SRAN AI Gateway — Seed demo data
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [ -d .venv ]; then
  source .venv/bin/activate
fi

echo "[*] Seeding demo data..."
python -m scripts.seed_demo_data
echo "[*] Done."
