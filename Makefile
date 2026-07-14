.PHONY: up prod prod-desktop down logs seed clean clean-portal-users fresh test test-unit test-all typecheck web-test web-e2e go-test ci shell-backend shell-admin shell-portal build rebuild ps help chat-edge-migrate chat-edge-migrate-main
.DEFAULT_GOAL := help

# Shell commands passed to `docker compose exec` run inside the
# running container. If the container isn't up yet, `make up` first.

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start the full stack in DEV mode (hot-reload, ports bound to 0.0.0.0)
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

clean: ## Stop the stack AND remove volumes (wipes DB + redis data)
	docker compose down -v

clean-portal-users: ## Delete portal users that aren't part of the seed set (keeps DB)
	docker compose exec backend python -m scripts.clean_portal_residue

fresh: clean ## NUCLEAR reset — wipe volumes + rebuild images + start + seed. Use this after adding/removing Next.js routes (stale .next cache survives plain restart and caused MODULE_NOT_FOUND on /requests post-UX-cleanup).
	docker compose build --no-cache admin-ui portal
	docker compose up -d
	@sleep 10
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
