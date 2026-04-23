# Secure AI Gateway Starter Kit

## Getting started (one command)

**Prerequisites:** Docker Desktop 4.27+ (Compose v2.24+) — that's it.

```bash
cp .env.example .env              # or copy/paste on Windows
make up                           # Linux/macOS
.\scripts\dev.ps1 up              # Windows PowerShell
make seed                         # first run only — creates admin + portal users
```

Open:
- **Admin UI** → http://localhost:3000 — login `admin / admin1234` (or `admin@audit.test / admin1234`)
- **Portal**   → http://localhost:3001 — login `alice@acme.test / Alice!2345`
- **Backend**  → http://localhost:8080/health
- **Mock LLM** → http://localhost:9090/health

The compose stack runs six services with hot-reload:

| Service       | Port | Image/Base                      |
|---------------|------|---------------------------------|
| postgres      | 5432 | postgres:16                     |
| redis         | 6379 | redis:7                         |
| mock-provider | 9090 | built from `./mock`             |
| backend       | 8080 | built from `./backend` + mount  |
| admin-ui      | 3000 | node:20-alpine + mount          |
| portal        | 3001 | node:20-alpine + mount          |

Code edits in `backend/`, `admin-ui/`, `portal/` are picked up without rebuild — uvicorn `--reload` for Python, `next dev` for both Next apps.

Other useful targets (`make help` for the full list):

| Command           | Effect                                    |
|-------------------|-------------------------------------------|
| `make down`       | Stop stack, keep volumes                  |
| `make clean`      | Stop + wipe DB/Redis volumes              |
| `make logs`       | Tail all service logs                     |
| `make seed`       | Re-run seed (idempotent)                  |
| `make test`       | Run backend pytest inside container       |
| `make shell-backend` | Exec into backend container            |

### Seeded test credentials

After `make seed`:
- **Admin** — `admin` / `admin1234` (super_admin)
- **Portal** — `alice@acme.test / Alice!2345`, `bob@globex.test / Bob!2345`, `charlie@acme.test / Charlie!2345` (low credit), `dave@acme.test / Dave!2345` (suspended), `eve@globex.test / Eve!2345` (email unverified)

---

A full project scaffold for a **multi-provider AI Gateway** with:
- unified API endpoint layer
- provider adapters
- security / DLP / masking
- routing engine
- governance policies
- observability and audit logging
- admin console scaffold
- infra and deployment skeletons

This repository is intentionally structured as a **serious starter architecture** rather than a finished production system.
Use it as the base for Claude or an engineering team to continue implementation.

## What is included
- FastAPI backend scaffold
- Next.js admin console scaffold
- PostgreSQL / Redis local dev stack
- docs for architecture, phases, security, routing, API design
- sample configs and seeds
- placeholder services and interfaces for future implementation

## What is not fully implemented yet
- real provider integrations
- production-grade DLP / PII detection
- full RBAC / SSO / billing
- full UI screens
- complete streaming and tool-calling compatibility

## Legacy "run locally without Docker" quick-start

The consolidated stack above is the supported path. If you need to run pieces on the host (e.g. for profiling):

```bash
# Backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080

# Admin UI
cd admin-ui && npm install && npm run dev

# Portal
cd portal && npm install && npm run dev
```

You still need postgres + redis running somewhere; the easiest way is `docker compose up -d postgres redis mock-provider` and leave the backend/frontends on the host.

## Suggested next implementation order
1. provider adapters
2. request orchestration flow
3. policy engine v1
4. PII masking v1
5. audit + usage tracking
6. admin UI wiring
7. semantic router
8. enterprise controls

## Monorepo layout
See `docs/REPO_MAP.md`.
