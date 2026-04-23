.PHONY: up down logs seed clean test shell-backend shell-admin shell-portal build rebuild ps help
.DEFAULT_GOAL := help

# Shell commands passed to `docker compose exec` run inside the
# running container. If the container isn't up yet, `make up` first.

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start the full stack in dev mode (hot-reload on backend/admin-ui/portal)
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

down: ## Stop the stack (preserves volumes)
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

seed: ## Seed tenants + projects + API keys + admin user + portal users
	docker compose exec backend python -m scripts.seed_audit_data

clean: ## Stop the stack AND remove volumes (wipes DB + redis data)
	docker compose down -v

test: ## Run the backend test suite inside the backend container
	docker compose exec backend pytest

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
