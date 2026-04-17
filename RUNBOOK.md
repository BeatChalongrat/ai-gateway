# SRAN AI Gateway — Runbook

## Quick Start (First Time)

```bash
# 1. Start PostgreSQL + Redis
docker compose up -d postgres redis

# 2. Configure environment
cp .env.example .env                      # Edit with your API keys
cp admin-ui/.env.example admin-ui/.env.local

# 3. Run everything
./scripts/start.sh        # Linux/macOS
.\scripts\start.ps1       # Windows PowerShell

# 4. Open browser
open http://localhost:3000
```

## Requirements

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.11+ | Backend runtime |
| Node.js | 18+ | Frontend build |
| PostgreSQL | 14+ | Primary database |
| Redis | 7+ | Rate limiting (optional) |
| Docker | 24+ | For running Postgres/Redis easily |

## Architecture

```
                    :3000              :8080              :5432
Browser ──→ [Next.js Admin UI] ──→ [FastAPI Backend] ──→ [PostgreSQL]
                                        │                     │
                                        ├──→ [Redis :6379]    │
                                        │    (rate limits)     │
                                        │                     │
                                        ├──→ OpenAI API       │
                                        ├──→ Anthropic API    │
                                        └──→ OpenRouter API   │
```

## Start Services Manually

### Infrastructure (Docker)

```bash
docker compose up -d postgres redis
```

### Backend

```bash
cd backend
python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 4 \
  --timeout-keep-alive 10 \
  > ../logs/backend.log 2>&1 &
```

### Frontend

```bash
cd admin-ui
npm run dev
# Runs on http://localhost:3000
```

## Database

### Run Migrations

```bash
./scripts/migrate.sh        # Linux/macOS
.\scripts\migrate.ps1       # Windows

# Or manually:
cd backend && alembic upgrade head
```

### Seed Demo Data

Creates: 1 demo tenant, 1 demo project, 1 demo API key (printed once).

```bash
./scripts/seed.sh           # Linux/macOS
.\scripts\seed.ps1          # Windows

# Or manually:
cd backend && python -m scripts.seed_demo_data
```

### Migration History

```
001_initial_schema.py       — request_logs, providers
002_add_tenants.py          — tenants table
003_add_api_keys.py         — api_keys table
004_add_usage_columns.py    — token/cost columns on request_logs
005_add_role_to_api_keys.py — role column (user/admin)
006_add_quota_fields.py     — quota enforcement fields
007_add_admin_audit_log.py  — admin_audit_log table
```

## Health Check

```bash
curl http://localhost:8080/health
# → {"status":"ok","checks":{"database":"ok","redis":"ok"}}
```

Health statuses: `ok` (all systems), `degraded` (Redis down, DB up).

## Metrics

```bash
curl http://localhost:8080/metrics
# → {"uptime_seconds":123,"total_requests":45,"requests_by_path":{...}}
```

## Load Test

```bash
python scripts/load_test.py
# 4 concurrent x 5 rounds, reports OK%, P50, P95, P99
```

## Smoke Test

```bash
./scripts/smoke_test.sh     # Linux/macOS
.\scripts\smoke_test.ps1    # Windows
```

Checks: backend health, tenants, models, providers, policies, frontend.

## Live Provider Test

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "ping"}]
  }'
```

## Environment Variables

### Backend (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Environment (`development`, `staging`, `production`) |
| `APP_PORT` | `8080` | Backend listen port |
| `LOG_LEVEL` | `INFO` | Log verbosity |
| `CORS_ORIGINS` | `http://localhost:3000,...` | Comma-separated allowed origins |
| `POSTGRES_HOST` | `localhost` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_DB` | `ai_gateway` | Database name |
| `POSTGRES_USER` | `postgres` | Database user |
| `POSTGRES_PASSWORD` | `postgres` | Database password |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `JWT_SECRET` | `change-me` | JWT signing key (must change in prod) |
| `MASTER_ENCRYPTION_KEY` | `change-me` | AES key for API key encryption |
| `OPENAI_API_KEY` | | OpenAI provider key |
| `ANTHROPIC_API_KEY` | | Anthropic provider key |
| `OPENROUTER_API_KEY` | | OpenRouter provider key |
| `PROVIDER_TIMEOUT_CONNECT` | `5.0` | Provider connection timeout (seconds) |
| `PROVIDER_TIMEOUT_READ` | `30.0` | Provider read timeout (seconds) |
| `PROVIDER_MAX_RETRIES` | `2` | Max retry attempts per provider call |
| `DLP_ENABLED` | `true` | Enable DLP scanning |
| `DLP_DEFAULT_PROFILE` | `default` | Default DLP policy profile |
| `DLP_DISABLED_DETECTORS` | | Comma-separated detectors to skip |
| `DLP_SCAN_RESPONSES` | `true` | Scan provider responses too |

### Frontend (`admin-ui/.env.local`)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8080` | Backend API URL |
| `NEXT_PUBLIC_ADMIN_API_KEY` | | Admin API key for console auth |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Backend hangs / no response | Restart with `--workers 4 --timeout-keep-alive 5 > /dev/null 2>&1 &` |
| CORS error in browser | Check `CORS_ORIGINS` in `.env` includes `http://localhost:3000` |
| Frontend "Failed to fetch" | Verify `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080` in `admin-ui/.env.local` |
| DB connection error | Check `POSTGRES_*` vars and that PostgreSQL is running: `docker compose up -d postgres` |
| `.next` cache error | `cd admin-ui && rm -rf .next && npm run dev` |
| "STARTUP BLOCKED" error | Set real values for `JWT_SECRET` / `MASTER_ENCRYPTION_KEY` or use `APP_ENV=development` |
| Admin endpoints return 401 | Frontend needs `NEXT_PUBLIC_ADMIN_API_KEY` set to a valid admin-role key |
| Admin endpoints return 403 | API key exists but has `role=user`. Update to `role=admin` in DB |
| Port already in use | `npx kill-port 8080` or `npx kill-port 3000` |
| Rate limit 429 | Default: 100 req/min per project. Adjust in `RateLimitService` constructor |
| Request timeout 504 | Backend enforces 20s timeout. Check provider latency or DB pool |
| All requests pending | Workers stalled by stale connections. Kill python, restart backend |

## Docker (Full Stack)

```bash
docker compose up --build
```

Starts: PostgreSQL (5432), Redis (6379), Backend (8080), Frontend (3000).

## Project Structure

```
ai-gateway/
  .env.example          # Backend env template
  docker-compose.yml    # Full stack containers
  Makefile              # Dev shortcuts: make backend, make admin
  RUNBOOK.md            # This file
  backend/
    app/
      main.py           # FastAPI entry point
      core/config.py    # All env var definitions (pydantic-settings)
      core/db.py        # SQLAlchemy engine + session
      api/routes/       # admin.py, public_chat.py
      services/         # Business logic
      models/           # SQLAlchemy ORM models
    migrations/         # Alembic migrations
    scripts/
      seed_demo_data.py # Demo data seeder
    requirements.txt
  admin-ui/
    app/                # Next.js App Router pages
    components/         # React components
    lib/                # API client, hooks, auth
    .env.example        # Frontend env template
  scripts/
    start.sh / .ps1     # Full startup script
    migrate.sh / .ps1   # DB migration
    seed.sh / .ps1      # Demo data seed
    smoke_test.sh / .ps1 # Automated health checks
    load_test.py        # Concurrent load test
  logs/                 # Runtime logs (gitignored)
```

## Phase 4 — Hardening Features

| Feature | Location | Notes |
|---------|----------|-------|
| DB connection pool | `core/db.py` | pool_size=10, max_overflow=20, pre_ping=True |
| Request timeout | `middleware/timeout.py` | 20s per request, returns 504 |
| Metrics | `middleware/metrics.py` + `GET /metrics` | Counts by path and status |
| Rate limiting | `services/rate_limit_service.py` | 100 req/min per project (in-memory) |
| Quota enforcement | `services/quota_service.py` | Daily request cap per project |
| Budget enforcement | `services/budget_service.py` | USD cost cap per project |
| Health checks | `api/routes/health.py` | DB + Redis connectivity checks |
