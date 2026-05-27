.PHONY: up prod down logs seed clean clean-portal-users fresh test shell-backend shell-admin shell-portal build rebuild ps help chat-edge-migrate
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
