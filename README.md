# Secure AI Gateway Starter Kit

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

## Quick start

### Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

### Admin UI
```bash
cd admin-ui
npm install
npm run dev
```

### Docker local stack
```bash
docker compose up --build
```

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
