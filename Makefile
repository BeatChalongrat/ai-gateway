.PHONY: up preflight prod prod-desktop down logs seed migrate clean clean-portal-users fresh bootstrap sandbox-up sandbox-image webchat-up webchat-down compose-check health test test-unit test-all typecheck web-test web-e2e go-test ci shell-backend shell-admin shell-portal build rebuild ps help chat-edge-migrate chat-edge-migrate-main
.DEFAULT_GOAL := help

# Shell commands passed to `docker compose exec` run inside the
# running container. If the container isn't up yet, `make up` first.

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Deployment shapes (they intentionally differ — do NOT merge the compose files):"
	@echo "  dev           make bootstrap   first-run: gen .env + up + health-gate + migrate + seed"
	@echo "                make up          start the dev stack (hot-reload; ports on 0.0.0.0)"
	@echo "  prod-desktop  make prod-desktop  dev-set + chat-edge + mcp-gateway + nginx (:80/:443, --profile desktop)"
	@echo "  webchat       make webchat-up  SEPARATE stack + its OWN postgres:5433 / redis:6380 / web-chat:3002"
	@echo "                                 code-interpreter sandbox is ON by default here (document reading,"
	@echo "                                 Office plan cards and run_python fail closed without it)"
	@echo "  sandbox       make sandbox-up  overlay for the BASE/desktop stack only — the webchat stack has it inline"

preflight: ## Verify .env has the hard-required vars (REDIS_PASSWORD, ...) before compose up — fails loud with a fix hint
	bash scripts/preflight.sh

up: preflight ## Start the full stack in DEV mode (hot-reload, ports bound to 0.0.0.0)
	docker compose up -d
	@echo ""
	@echo "Backend:   http://localhost:8080/health"
	@echo "Admin UI:  http://localhost:3000"
	@echo "Portal:    http://localhost:3001"
	@echo "Mock prov: http://localhost:9090/health"
	@echo "Postgres:  localhost:5432 (postgres/postgres)"
	@echo "Redis:     localhost:6379"
	@echo ""
	@echo "Run 'make seed' once to create admin + test portal users."

bootstrap: ## FIRST-RUN (idempotent): generate .env + up + health-gate + chat-edge migrate + seed. Safe to re-run.
	bash scripts/bootstrap.sh

health: ## Actively poll the dev stack until postgres/redis/backend/gateway are ready (read-only; safe on a live stack)
	bash scripts/wait_for_health.sh

prod: ## Start the stack in PROD mode (binds 127.0.0.1; requires REDIS_PASSWORD, CORS_ORIGINS, PORTAL_PUBLIC_URL, INITIAL_ADMIN_PASSWORD in .env)
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
	@echo ""
	@echo "Stack running. All services bound to 127.0.0.1 only."
	@echo "Front the public services with a reverse proxy on the host:"
	@echo "  127.0.0.1:8080  -> backend  (e.g. https://gateway-api.sran.net)"
	@echo "  127.0.0.1:3000  -> admin-ui (e.g. https://admin.sran.net)"
	@echo "  127.0.0.1:3001  -> portal   (e.g. https://portal.sran.net)"
	@echo ""
	@echo "If this is a fresh DB, run 'make seed' (uses INITIAL_ADMIN_PASSWORD from .env)."

down: ## Stop the stack (preserves volumes; works for both dev and prod stacks)
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

seed: ## Seed tenants + projects + API keys + admin user + portal users
	docker compose exec backend python -m scripts.seed_audit_data

NER ?= off

dlp-eval: ## FIX28: DLP detection-quality eval — both engines (Python + live Rust sidecar), must-catch recall per category + must-not-fire corpus, checked against backend/evals/dlp/baseline.json
	docker exec ai-gateway-backend-1 sh -c 'cd /app && python -m evals.dlp.run --require-rust --check-baseline'

dlp-eval-baseline: ## FIX28: re-record the DLP eval baseline (deliberate; review the diff before committing)
	docker exec ai-gateway-backend-1 sh -c 'cd /app && python -m evals.dlp.run --require-rust --write-baseline'

mask-eval: ## FIX37: mask-quality eval — detection + comprehension (axis A) + re-identification (axis B) on the synthetic Thai corpus. Needs GATEWAY_API_KEY in .env for the scan-rounds stage.
	docker exec ai-gateway-backend-1 sh -c 'cd /app && python -m evals.mask_quality.run --stage all --ner $(NER) --out /tmp/mask_eval.json'

mask-eval-detect: ## FIX37: detection-only slice of the mask-quality eval (no model calls, seconds not minutes)
	docker exec ai-gateway-backend-1 sh -c 'cd /app && python -m evals.mask_quality.run --stage detect --ner $(NER)'

migrate: ## Apply pending BACKEND (gateway) Alembic migrations to the running postgres. Run this when a long-lived container outlived a new migration (schema-drift guard names the gap).
	docker compose exec backend python -m alembic upgrade head

clean: ## Stop the stack AND remove volumes (wipes DB + redis data)
	docker compose down -v

clean-portal-users: ## Delete portal users that aren't part of the seed set (keeps DB)
	docker compose exec backend python -m scripts.clean_portal_residue

fresh: clean ## NUCLEAR reset — wipe volumes + rebuild images + start + seed. Use this after adding/removing Next.js routes (stale .next cache survives plain restart and caused MODULE_NOT_FOUND on /requests post-UX-cleanup).
	docker compose build --no-cache admin-ui portal
	docker compose up -d
	@bash scripts/wait_for_health.sh
	@$(MAKE) seed
	@echo ""
	@echo "System fresh. Open http://localhost:3000"

test: ## Run the backend test suite inside the backend container
	# Config / dlp tests assert pydantic Settings defaults. The compose
	# runtime sets POSTGRES_HOST=postgres etc.; `env -u` unsets those
	# so the defaults come from the Settings class, not the container env.
	docker compose exec backend env \
	  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
	  -u POSTGRES_USER -u POSTGRES_PASSWORD \
	  -u REDIS_HOST -u REDIS_PORT \
	  -u CORS_ORIGINS -u LOG_LEVEL \
	  -u PROVIDER_BASE_URL_OVERRIDES -u DLP_DISABLED_DETECTORS \
	  pytest

test-unit: ## Backend UNIT lane only (excludes integration) — the CI gate
	docker compose exec backend pytest -m "not integration"

test-all: ## Full backend suite (includes integration — needs the live stack)
	docker compose exec backend pytest

typecheck: ## TypeScript typecheck for the web-chat frontend
	cd apps/web-chat && npx tsc --noEmit

web-test: ## Vitest unit lanes for the three web apps (runs on the HOST — node_modules in Docker is a named volume the host can't reach)
	cd apps/web-chat && npm test
	cd portal && npm test
	cd admin-ui && npm test

web-e2e: ## Playwright happy path for web-chat (backends mocked; boots its own dev server on :3102)
	cd apps/web-chat && npm run test:e2e

go-test: ## Vet + test the chat-edge-api Go service
	cd services/chat-edge-api && go vet ./... && go test ./...

ci: test-unit typecheck web-test go-test ## Run the full local CI gate before pushing (mirrors .github/workflows/ci.yml)

build: ## Build all images without starting
	docker compose build

rebuild: ## Force-rebuild all images from scratch (no cache)
	docker compose build --no-cache

ps: ## Show running containers + health status
	docker compose ps

shell-backend: ## Exec into the backend container
	docker compose exec backend /bin/bash

shell-admin: ## Exec into the admin-ui container
	docker compose exec admin-ui /bin/sh

shell-portal: ## Exec into the portal container
	docker compose exec portal /bin/sh

chat-edge-migrate: ## Apply chat-edge SQL migrations to the running postgres-webchat (idempotent; needed after pulling new migrations because docker initdb only runs on first volume init)
	bash services/chat-edge-api/scripts/apply_migrations.sh

# ── Desktop production deploy (R11 B6) ─────────────────────────────
prod-desktop: ## Deploy the full stack with desktop support (chat-edge + MCP + nginx; profile "desktop")
	@test -f .env || (echo "ERROR: .env not found. Copy infra/production/.env.production.template to .env and fill it in." && exit 1)
	docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile desktop up -d --build
	@echo ""
	@echo "Stack is up (all behind nginx :80/:443). Public paths:"
	@echo "  Gateway:       https://<host>/v1/          (+ /__gw/health)"
	@echo "  Chat Edge API: https://<host>/api/v1/      (+ /api/v1/health)"
	@echo "  Portal:        https://<host>/"
	@echo "  Admin:         https://<host>/admin/"
	@echo "  MCP catalog:   https://<host>/mcp/catalog.json"
	@echo "  Releases:      https://<host>/desktop/latest.json"
	@echo ""
	@echo "Desktop app: enter https://<host> as the server URL in first-run setup."
	@echo "Fresh DB? Run 'make seed'. Pulled new chat-edge migrations? Run 'make chat-edge-migrate-main'."

chat-edge-migrate-main: ## Apply chat-edge SQL migrations to the MAIN-stack postgres (the init hook only runs on first volume init)
	bash scripts/apply_chat_edge_migrations_main.sh

# ── Web-chat stack (sandbox ON by default) ─────────────────────────
# docker-compose.webchat.yml now carries the sandbox env + docker-socket
# mount inline (it used to be the opt-in docker-compose.sandbox.yml overlay).
# Reason: with the fail-closed chat-edge services, "sandbox off" is not a
# degraded mode — text extraction from PDF/DOCX/XLSX, every Office plan card,
# version compare, large-image vision and run_python all return errors. The
# stack's default had to match what the product actually needs.
#
# The one prerequisite is the per-run image: chat-edge fail-LOUDS (exit 1) if
# SANDBOX_ENABLED is on and ai-gateway-sandbox:latest is absent, so the
# bring-up target below builds it FIRST. Bringing the stack up by hand with a
# bare `docker compose -f docker-compose.webchat.yml up -d` still works — but
# only after `make sandbox-image`, otherwise chat-edge exits on start.
WEBCHAT_COMPOSE := -f docker-compose.webchat.yml

sandbox-image: ## Build the per-run code-interpreter image (ai-gateway-sandbox:latest) — prerequisite for any stack with SANDBOX_ENABLED on
	docker build -f services/chat-edge-api/sandbox/Dockerfile.sandbox -t ai-gateway-sandbox:latest services/chat-edge-api

webchat-up: sandbox-image ## Start the web-chat stack (chat-edge:4000 + web-chat:3002 + own pg:5433/redis:6380) WITH the code-interpreter sandbox enabled
	@test -f .env || (echo "ERROR: .env not found. Run 'make bootstrap' first." && exit 1)
	@echo "════════════════════════════════════════════════════════════════"
	@echo " DOCKER-SOCKET PRIVILEGE NOTICE"
	@echo " This stack bind-mounts /var/run/docker.sock into chat-edge — that"
	@echo " is HOST-ROOT-EQUIVALENT. It is acceptable ONLY because the docker"
	@echo " run argv is built entirely from trusted server constants; no model"
	@echo " input ever reaches it (model code runs INSIDE the no-privilege"
	@echo " sandbox). See the header in docker-compose.webchat.yml."
	@echo " Opt out with SANDBOX_ENABLED=0 — document reading, the Office plan"
	@echo " cards, compare and run_python go with it (they fail closed)."
	@echo "════════════════════════════════════════════════════════════════"
	@mkdir -p ./data/sandbox-runs
	docker compose -p ai-gateway $(WEBCHAT_COMPOSE) up -d --build
	@bash scripts/sandbox_healthcheck.sh
	@echo ""
	@echo "Web chat:  http://localhost:3002   (log in with portal credentials)"
	@echo "Chat edge: http://localhost:4000/health"

webchat-down: ## Stop the web-chat stack (preserves volumes)
	docker compose -p ai-gateway $(WEBCHAT_COMPOSE) down

# ── Compose-shape validation (Batch 2 T0) ──────────────────────────
# The compose files are SHAPES, not freely-stackable layers: base(+override)
# is the core dev stack, webchat.yml is a SELF-CONTAINED stack, prod overlays
# base. Base and webchat must never be merged — base marks chat-edge-api
# ``profiles: ["desktop"]``, so the merged project drops the service and
# fails with "web-chat depends on undefined service chat-edge-api".
# This target validates every SUPPORTED shape so a compose edit that breaks
# one fails here instead of at the next bring-up. Prod shapes validate with
# placeholder values for their ``:?``-required vars (structure check only —
# real deploys still need the .env values).
PROD_CONFIG_ENV := WEB_CHAT_PUBLIC_URL=placeholder PORTAL_PUBLIC_URL=placeholder \
	CHAT_EDGE_ADMIN_API_KEY=placeholder MCP_PUBLIC_BASE=placeholder \
	CHAT_CORS_ORIGINS=placeholder CORS_ORIGINS=placeholder CSRF_SECRET=placeholder \
	PORTAL_JWT_SECRET=placeholder REDIS_PASSWORD=placeholder \
	SERVICE_JWT_SECRET=placeholder USER_JWT_SECRET=placeholder

compose-check: ## Validate every supported compose shape (dev / prod / prod-desktop / webchat / webchat+sandbox / desktop+sandbox)
	@echo "[1/6] dev: base + override" && docker compose -f docker-compose.yml -f docker-compose.override.yml config -q
	@echo "[2/6] prod: base + prod" && $(PROD_CONFIG_ENV) docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q
	@echo "[3/6] prod-desktop: base + prod + profile desktop" && $(PROD_CONFIG_ENV) docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile desktop config -q
	@echo "[4/6] webchat: webchat.yml alone" && docker compose $(WEBCHAT_COMPOSE) config -q
	@echo "[5/6] webchat + sandbox overlay" && docker compose $(SANDBOX_COMPOSE) config -q
	@echo "[6/6] desktop + sandbox overlay" && docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.sandbox.yml --profile desktop config -q
	@echo ""
	@echo "All supported compose shapes validate."
	@echo "NOT supported: merging docker-compose.yml with docker-compose.webchat.yml"
	@echo "(chat-edge-api is profile-gated in base -> 'web-chat depends on undefined"
	@echo "service chat-edge-api'). Use 'make webchat-up' / 'make up' instead."

# ── Code-interpreter sandbox overlay (Phase 13 / 15) ───────────────
# Kept for the BASE/desktop stack, where chat-edge-api is profile-gated and
# does NOT get the inline sandbox wiring the webchat file now has. Layering it
# on the webchat stack is a redundant no-op (identical values) — harmless.
# The sandbox overlay adds env + the docker-socket mount to chat-edge-api.
# We layer it on the WEBCHAT stack (which owns the running
# ai-gateway-chat-edge-api container) — NOT the base file: base marks
# chat-edge-api `profiles:[desktop]`, so base+webchat is an invalid project
# (web-chat then "depends on undefined service chat-edge-api"). Same project
# name (ai-gateway) so this recreates the existing container in place; other
# services are left untouched (no --remove-orphans).
SANDBOX_COMPOSE := -f docker-compose.webchat.yml -f docker-compose.sandbox.yml

sandbox-up: ## Enable the hardened run_python sandbox on chat-edge (builds sandbox image, layers docker-compose.sandbox.yml, SANDBOX_ENABLED=1). OPTS INTO the docker-socket mount.
	@test -f .env || (echo "ERROR: .env not found. Run 'make bootstrap' first." && exit 1)
	@echo "════════════════════════════════════════════════════════════════"
	@echo " DOCKER-SOCKET PRIVILEGE WARNING (conscious opt-in)"
	@echo " sandbox-up bind-mounts /var/run/docker.sock into chat-edge — that"
	@echo " is HOST-ROOT-EQUIVALENT. It is acceptable ONLY because the docker"
	@echo " run argv is built entirely from trusted server constants; no model"
	@echo " input ever reaches it (model code runs INSIDE the no-privilege"
	@echo " sandbox). See docker-compose.sandbox.yml header for the details."
	@echo "════════════════════════════════════════════════════════════════"
	@mkdir -p ./data/sandbox-runs
	@echo "[sandbox] 1/3 Building the per-run sandbox image (ai-gateway-sandbox:latest)..."
	docker build -f services/chat-edge-api/sandbox/Dockerfile.sandbox -t ai-gateway-sandbox:latest services/chat-edge-api
	@echo "[sandbox] 2/3 Rebuilding chat-edge-api (image now bundles docker-cli)..."
	docker compose -p ai-gateway $(SANDBOX_COMPOSE) build chat-edge-api
	@echo "[sandbox] 3/3 (Re)starting chat-edge-api with the sandbox overlay..."
	docker compose -p ai-gateway $(SANDBOX_COMPOSE) up -d chat-edge-api
	@bash scripts/sandbox_healthcheck.sh
