#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# `make bootstrap` — one-command first-run for the DEV stack (Phase 15).
# Idempotent end to end: safe to re-run on an already-provisioned box.
#
#   1. Generate .env (if absent) with strong secrets — scripts/gen_env.sh
#   2. docker compose up -d              (dev stack; hot-reload overlay)
#   3. Health-gate every critical svc    — scripts/wait_for_health.sh
#   4. Apply chat-edge SQL migrations     (idempotent IF NOT EXISTS)
#   5. Seed admin+portal ONLY if not seeded (query admin first)
#   6. Print a clear "what's up / what's not" summary
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "════════════════════════════════════════════════════════════════"
echo " SRAN AI Gateway — bootstrap (first-run, idempotent)"
echo "════════════════════════════════════════════════════════════════"

# 1. .env ------------------------------------------------------------------
bash scripts/gen_env.sh

# 2. bring up the dev stack ------------------------------------------------
echo ""
echo "[bootstrap] Starting the dev stack (docker compose up -d)..."
docker compose up -d

# 3. health gate (replaces sleep) ------------------------------------------
echo ""
bash scripts/wait_for_health.sh

# 4. chat-edge migrations --------------------------------------------------
# The Go chat-edge binary VERIFIES but never APPLIES migrations. Fresh
# volumes get them from the postgres init hook; an existing-volume operator
# would otherwise be left fail-closed — so apply them here every run
# (statements are CREATE ... IF NOT EXISTS, so re-application is a no-op).
echo ""
echo "[bootstrap] Applying chat-edge SQL migrations to the main postgres (idempotent)..."
bash scripts/apply_chat_edge_migrations_main.sh \
  || echo "[bootstrap] NOTE: migration step reported errors (usually 'already applied') — review the output above."

# 5. idempotent seed -------------------------------------------------------
echo ""
echo "[bootstrap] Checking whether the admin user is already seeded..."
SEEDED="$(docker compose exec -T backend python -c \
  "from app.core.db import SessionLocal; from app.models.admin_user import AdminUserORM; from sqlalchemy import select; s=SessionLocal(); print('SEEDED' if s.execute(select(AdminUserORM).where(AdminUserORM.username=='admin')).first() else 'EMPTY')" \
  2>/dev/null | tr -d '[:space:]' || true)"

if [ "$SEEDED" = "SEEDED" ]; then
  echo "[bootstrap] Admin user already present — skipping seed."
else
  echo "[bootstrap] No admin user found — seeding tenants + projects + API keys + admin + portal users..."
  docker compose exec -T backend python -m scripts.seed_audit_data
fi

# 6. summary ---------------------------------------------------------------
cat <<'EOF'

════════════════════════════════════════════════════════════════
 Bootstrap complete — DEV stack is UP
════════════════════════════════════════════════════════════════
  Gateway (public):   http://localhost:8080/__gw/health
  Backend (direct):   http://localhost:8081/health
  Admin UI:           http://localhost:3000     (login: admin / admin1234)
  Portal:             http://localhost:3001
  Mock provider:      http://localhost:9090/health
  Prometheus:         http://localhost:9091
  Grafana:            http://localhost:3100     (admin / admin)
  Postgres / Redis:   in-network only (no host port published in dev)

  NOT started by bootstrap — add when you need them:
    Desktop shape (chat-edge + mcp-gateway + nginx):
        docker compose --profile desktop up -d     (dev, additive)
        make prod-desktop                          (prod, behind nginx TLS)
    Web-chat stack (its OWN postgres:5433 / redis:6380 / web UI :3002):
        docker compose -f docker-compose.webchat.yml up -d
    Code-interpreter sandbox overlay (docker-socket opt-in):
        make sandbox-up

  Run 'make help' for the full deployment-shape map.
════════════════════════════════════════════════════════════════
EOF
