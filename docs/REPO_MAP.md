# Repository Map

## root
- `backend/` FastAPI services
- `admin-ui/` Next.js admin console
- `docs/` architecture and implementation guides
- `infra/` deployment artifacts
- `examples/` sample requests and policies
- `scripts/` helper scripts

## backend/app
- `api/` public and admin routes
- `core/` config, logging, security basics
- `domain/` entities and business models
- `schemas/` pydantic request/response schemas
- `services/` orchestration, providers, policies, DLP, usage
- `repositories/` data access abstraction
- `workers/` async/background jobs
- `middleware/` auth, tracing, request context

## admin-ui
- `app/` route-based UI
- `components/` shared UI pieces
- `lib/` API client + helpers
