#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# `make bootstrap` — one-command first-run for the DEV stack (Phase 15).
# Idempotent end to end: safe to re-run on an already-provisioned box.
#
#   1. Generate .env (if absent) with strong secrets — scripts/gen_env.sh
#   2. Apply BACKEND migrations           (one-shot container, no app import)
#   3. docker compose up -d              (dev stack; backend = its image, FIX131)
#   4. Health-gate every critical svc    — scripts/wait_for_health.sh
#   5. Apply chat-edge SQL migrations     (idempotent IF NOT EXISTS)
#   6. Seed admin+portal ONLY if not seeded (query admin first)
#   7. Print a clear "what's up / what's not" summary
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "════════════════════════════════════════════════════════════════"
echo " SRAN AI Gateway — bootstrap (first-run, idempotent)"
echo "════════════════════════════════════════════════════════════════"

# 1. .env ------------------------------------------------------------------
# gen_env creates a fresh .env OR backfills missing required vars into an
# existing one (FIX 5, Bug B) — so an old .env predating a required var gets
# unstuck by re-running bootstrap.
bash scripts/gen_env.sh

# 1b. preflight — fail loud with a fix hint if a required var is still absent
#     (belt-and-suspenders: gen_env should have ensured them).
bash scripts/preflight.sh

# 2. backend migrations ----------------------------------------------------
# FIX90 — an EXPLICIT step, because the schema is no longer allowed to
# appear as a side effect. The dev backend's `command:` does run
# `alembic upgrade head` at container start, but only at container START:
# an existing container that `up -d` does not recreate keeps a schema older
# than the code, and the seed below would then hit a missing column. The
# guard's default is now `failclosed` (it refuses rather than migrating
# behind your back), so nothing else would apply them.
#
# A one-shot container, not `exec`: it needs no running backend (that is
# the whole point) and it replaces the service command, so `app.main` — and
# with it the startup guard — is never imported and cannot deadlock. It
# only needs postgres, which compose starts as its dependency. Idempotent:
# on an already-migrated database alembic prints nothing and exits 0.
echo ""
echo "[bootstrap] Applying backend (gateway) migrations — one-shot, before anything boots..."
docker compose run --rm backend python -m alembic upgrade head

# 3. bring up the dev stack ------------------------------------------------
echo ""
echo "[bootstrap] Starting the dev stack (docker compose up -d)..."
docker compose up -d

# 4. health gate (replaces sleep) ------------------------------------------
echo ""
bash scripts/wait_for_health.sh

# 5. chat-edge migrations --------------------------------------------------
# The Go chat-edge binary VERIFIES but never APPLIES migrations. Fresh
# volumes get them from the postgres init hook; an existing-volume operator
# would otherwise be left fail-closed — so apply them here every run
# (statements are CREATE ... IF NOT EXISTS, so re-application is a no-op).
echo ""
echo "[bootstrap] Applying chat-edge SQL migrations to the main postgres (idempotent)..."
bash scripts/apply_chat_edge_migrations_main.sh \
  || echo "[bootstrap] NOTE: migration step reported errors (usually 'already applied') — review the output above."

# 6. idempotent seed -------------------------------------------------------
echo ""
echo "[bootstrap] Checking whether the admin user is already seeded..."
SEEDED="$(docker compose exec -T backend python -c \
  "from app.core.db import SessionLocal; from app.models.admin_user import AdminUserORM; from sqlalchemy import select; s=SessionLocal(); print('SEEDED' if s.execute(select(AdminUserORM).limit(1)).first() else 'EMPTY')" \
  2>/dev/null | tr -d '[:space:]' || true)"

if [ "$SEEDED" = "SEEDED" ]; then
  echo "[bootstrap] Admin user already present — skipping seed."
else
  echo "[bootstrap] No admin user found — seeding tenants + projects + API keys + admin + portal users..."
  docker compose exec -T backend python -m scripts.seed_audit_data
fi

# 7. summary ---------------------------------------------------------------
cat <<'EOF'

════════════════════════════════════════════════════════════════
 Bootstrap complete — DEV stack is UP
════════════════════════════════════════════════════════════════
  Gateway (public):   https://localhost:8080/__gw/health
  Backend (direct):   http://localhost:8081/health
  Admin UI:           https://localhost:3000     (login: INITIAL_ADMIN_EMAIL of .env / admin1234 in dev)
  Portal:             https://localhost:3001
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
