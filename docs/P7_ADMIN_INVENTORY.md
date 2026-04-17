# P7-01: Admin Console Inventory & Gap Analysis

## Current State

### Backend Admin Endpoints (10 endpoints under /admin)

| # | Method | Path | Status | Data Source |
|---|--------|------|--------|-------------|
| 1 | GET | `/admin/providers` | Hardcoded | Static list |
| 2 | GET | `/admin/policies` | Hardcoded | Static list |
| 3 | GET | `/admin/requests` | **DB-backed** | RequestLogORM |
| 4 | GET | `/admin/tenants` | **DB-backed** | TenantORM |
| 5 | POST | `/admin/tenants` | **DB-backed** | Creates TenantORM |
| 6 | GET | `/admin/projects` | **DB-backed** | ProjectORM |
| 7 | POST | `/admin/projects` | **DB-backed** | Creates ProjectORM |
| 8 | GET | `/admin/api-keys` | **DB-backed** | ApiKeyORM |
| 9 | POST | `/admin/api-keys` | **DB-backed** | Creates ApiKeyORM |
| 10 | DELETE | `/admin/api-keys/{key_id}` | **DB-backed** | Revokes key |

### Frontend (admin-ui/)

- **Stack:** Next.js 14, React 18, TypeScript
- **Pages:** 7 stub pages (dashboard, providers, models, policies, requests, audit, usage)
- **Implementation:** 0% — all pages are TODO placeholders
- **Styling:** No CSS framework installed
- **API client:** `lib/api.ts` exists but unused

### Missing Backend Endpoints

| Endpoint | Purpose | Data Source |
|----------|---------|-------------|
| `GET /admin/models` | Model registry with capabilities | `model_registry` |
| `GET /admin/usage/summary` | Usage aggregation | `RequestLogService` |
| `GET /admin/requests/{request_id}` | Request detail | `RequestLogORM` |

### Missing Infrastructure

| Item | Status | Impact |
|------|--------|--------|
| CORS middleware | Config exists, **not wired** in `main.py` | Frontend can't call backend |
| Admin auth | None | All admin endpoints are open |

## Gap Analysis

### GAP-A1: CORS Not Wired
Backend has `cors_origins` in config but `CORSMiddleware` is not added to the FastAPI app. Any frontend will be blocked by browser CORS policy.
**Fix (P7-02):** Wire CORS middleware.

### GAP-A2: No Models Endpoint for Admin
`/v1/models` returns OpenAI-compatible format. Admin needs richer data (capabilities, cost tier, streaming support).
**Fix (P7-05):** Add `GET /admin/models` returning `ModelCandidate` data.

### GAP-A3: No Usage Summary Endpoint
`RequestLogService.usage_summary_by_project()` exists but no endpoint exposes it.
**Fix (P7-07):** Add `GET /admin/usage/summary` with project/tenant filtering.

### GAP-A4: No Request Detail Endpoint
`RequestLogService.get_by_id()` exists but no admin endpoint for single request detail.
**Fix (P7-06):** Add `GET /admin/requests/{request_id}`.

### GAP-A5: Providers Endpoint Hardcoded
`/admin/providers` returns static data. Should reflect actual adapter registration.
**Fix (P7-05):** Wire to real adapter registry.

### GAP-A6: Frontend Not Implemented
All pages are stubs. No components, no data fetching, no styling.
**Scope note:** Full React frontend implementation is deferred. Phase 7 focuses on backend completeness + a minimal functional admin page served by FastAPI for immediate operator use.

## Revised Execution Plan

Phase 7 will focus on **backend admin API completeness** and a **minimal server-rendered admin dashboard** (HTML templates via FastAPI) for immediate operator visibility — rather than a full React SPA rebuild.

| Task | Focus |
|------|-------|
| **P7-02** | Wire CORS + add admin API base URL config |
| **P7-03** | Backend: tenant/project CRUD completeness |
| **P7-04** | Backend: API key management completeness |
| **P7-05** | Backend: providers/models endpoint from real registry |
| **P7-06** | Backend: request detail endpoint + filtering |
| **P7-07** | Backend: usage summary endpoint |
| **P7-08** | Backend: response pagination across admin endpoints |
| **P7-09** | End-to-end admin API integration tests |
| **P7-10** | Admin API docs |
