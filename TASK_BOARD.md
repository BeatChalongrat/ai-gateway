# AI Gateway — Task Board

> Quick-reference tracking board. Update status as tasks complete.
> Full details for each task are in `IMPLEMENTATION_ROADMAP.md`.

**Status:** `[ ]` pending | `[>]` in progress | `[x]` done | `[-]` skipped

---

## Phase 0: Foundation Hardening

### 0A: Alembic + DB Migrations `[NEXT]`
- [ ] P0A-01 — Create `migrations/env.py`
- [ ] P0A-02 — Create `migrations/script.py.mako`
- [ ] P0A-03 — Create `models/__init__.py` (register all ORM models)
- [ ] P0A-04 — Add `created_at` / `updated_at` to all 3 ORM models
- [ ] P0A-05 — Verify `alembic.ini` config
- [ ] P0A-06 — Create initial migration `001_initial_schema.py`
- [ ] P0A-07 — Test upgrade/downgrade cycle

### 0B: Repository Layer `[PENDING]`
- [ ] P0B-01 — Implement `BaseRepository`
- [ ] P0B-02 — Implement `ProjectRepository`
- [ ] P0B-03 — Implement `ProviderRepository`
- [ ] P0B-04 — Implement `RequestLogRepository`
- [ ] P0B-05 — Add `get_db` session dependency
- [ ] P0B-06 — Write repository unit tests
- [ ] P0B-07 — Create `tests/conftest.py` with DB fixtures

### 0C: Error Handling + Async `[PENDING]`
- [ ] P0C-01 — Create `core/exceptions.py` (exception hierarchy)
- [ ] P0C-02 — Create `middleware/error_handler.py`
- [ ] P0C-03 — Register error handler in `main.py`
- [ ] P0C-04 — Convert orchestrator to async + try/except per step
- [ ] P0C-05 — Convert all provider adapters + dispatcher to async
- [ ] P0C-06 — Convert `public_chat.py` route to async
- [ ] P0C-07 — Write error handling tests
- [ ] P0C-08 — Regression test: happy-path chat

### 0D: Config + Environment `[PENDING]`
- [ ] P0D-01 — Expand `Settings` class with all config fields
- [ ] P0D-02 — Update `.env.example` files
- [ ] P0D-03 — Add `GET /health/deep` (DB + Redis checks)
- [ ] P0D-04 — Add CORS middleware from settings
- [ ] P0D-05 — Write config validation tests

---

## Phase 1: Unified API + Provider Adapters

### 1A: Provider Response Contract `[PENDING]`
- [ ] P1A-01 — Define `ProviderResponse` dataclass
- [ ] P1A-02 — Update `BaseProviderAdapter` return type
- [ ] P1A-03 — Update adapter stubs to return `ProviderResponse`
- [ ] P1A-04 — Update orchestrator to consume `ProviderResponse`
- [ ] P1A-05 — Update dispatcher type

### 1B: OpenAI Adapter `[PENDING]`
- [ ] P1B-01 — Add `openai` to requirements
- [ ] P1B-02 — Implement real `OpenAIAdapter.chat()`
- [ ] P1B-03 — Error mapping (OpenAI → Gateway errors)
- [ ] P1B-04 — Mock tests: request/response mapping
- [ ] P1B-05 — Mock tests: error scenarios

### 1C: Anthropic Adapter `[PENDING]`
- [ ] P1C-01 — Add `anthropic` to requirements
- [ ] P1C-02 — Implement real `AnthropicAdapter.chat()`
- [ ] P1C-03 — System message extraction
- [ ] P1C-04 — Error mapping
- [ ] P1C-05 — Mock tests

### 1D: OpenRouter Adapter `[PENDING]`
- [ ] P1D-01 — Implement `OpenRouterAdapter.chat()` with httpx
- [ ] P1D-02 — Error mapping
- [ ] P1D-03 — Mock tests

### 1E: Orchestrator Retry + Timeout `[PENDING]`
- [ ] P1E-01 — Timeout wrapper on provider dispatch
- [ ] P1E-02 — Retry with exponential backoff
- [ ] P1E-03 — `select_fallback_route()` in routing service
- [ ] P1E-04 — Wire retry + fallback into orchestrator
- [ ] P1E-05 — Retry/fallback tests

### 1F: Streaming (SSE) `[PENDING]`
- [ ] P1F-01 — `chat_stream()` on base adapter
- [ ] P1F-02 — OpenAI streaming
- [ ] P1F-03 — Anthropic streaming
- [ ] P1F-04 — Streaming path in orchestrator
- [ ] P1F-05 — `StreamingResponse` from route
- [ ] P1F-06 — Streaming tests

---

## Phase 2: Tenancy + Auth

### 2A: Tenant + API Key Data Model `[PENDING]`
- [ ] P2A-01 — `TenantORM` model
- [ ] P2A-02 — `UserORM` model
- [ ] P2A-03 — `ApiKeyORM` model
- [ ] P2A-04 — FK from Project → Tenant
- [ ] P2A-05 — Alembic migration
- [ ] P2A-06 — Test migration

### 2B: API Key Hashing + Validation `[PENDING]`
- [ ] P2B-01 — `hash_api_key()` with SHA-256
- [ ] P2B-02 — Key generation utility
- [ ] P2B-03 — `ApiKeyRepository` with hash lookup
- [ ] P2B-04 — Wire validation into auth dependency
- [ ] P2B-05 — Auth tests

### 2C: Project Resolution from DB `[PENDING]`
- [ ] P2C-01 — Rewrite `ProjectService` to use DB
- [ ] P2C-02 — Inject DB session into orchestrator
- [ ] P2C-03 — Project resolution tests

### 2D: Seed Data + Bootstrap `[PENDING]`
- [ ] P2D-01 — Implement `seed_demo_data.py`
- [ ] P2D-02 — Make seed idempotent
- [ ] P2D-03 — Update bootstrap scripts + Makefile
- [ ] P2D-04 — End-to-end test: seed → auth → chat

---

## Phase 3: Policy Engine + DLP

### 3A: Policy Profile Data Model `[PENDING]`
- [ ] P3A-01 — `PolicyProfileORM` model
- [ ] P3A-02 — `PolicyProfileRepository`
- [ ] P3A-03 — Alembic migration
- [ ] P3A-04 — Repository tests

### 3B: Policy Engine DB Integration `[PENDING]`
- [ ] P3B-01 — Rewrite policy engine to load from DB
- [ ] P3B-02 — Rule evaluators (allowed_models, blocked_data, pii, max_tokens)
- [ ] P3B-03 — Mode logic (enforce/audit/disabled)
- [ ] P3B-04 — Policy engine tests

### 3C: Expanded DLP Patterns `[PENDING]`
- [ ] P3C-01 — SSN pattern
- [ ] P3C-02 — Credit card + Luhn
- [ ] P3C-03 — AWS key detection
- [ ] P3C-04 — Generic secret detection
- [ ] P3C-05 — DLP test suite

### 3D: Response-Side DLP `[PENDING]`
- [ ] P3D-01 — `scan_response()` method
- [ ] P3D-02 — Wire into orchestrator
- [ ] P3D-03 — Log response DLP findings
- [ ] P3D-04 — Response scanning tests

---

## Phase 4: Routing Engine

### 4A: Model Registry `[PENDING]`
- [ ] P4A-01 — `ModelRegistryORM` model
- [ ] P4A-02 — `ModelRepository`
- [ ] P4A-03 — Alembic migration
- [ ] P4A-04 — Seed common models with pricing
- [ ] P4A-05 — Repository tests

### 4B: Registry-Based Routing `[PENDING]`
- [ ] P4B-01 — Rewrite routing to use registry
- [ ] P4B-02 — Auto-routing: capability → cost sort
- [ ] P4B-03 — Explicit model validation
- [ ] P4B-04 — Policy-allowed model filtering
- [ ] P4B-05 — Routing tests

### 4C: Fallback Chains `[PENDING]`
- [ ] P4C-01 — `FallbackChain` entity
- [ ] P4C-02 — Chain traversal logic
- [ ] P4C-03 — Wire into orchestrator
- [ ] P4C-04 — Fallback tests

### 4D: Cost-Aware Routing `[PENDING]`
- [ ] P4D-01 — Cost-aware ranking
- [ ] P4D-02 — Usage service with per-model pricing
- [ ] P4D-03 — Cost routing tests

---

## Phase 5: Observability + Audit

### 5A: Audit Event Persistence `[PENDING]`
- [ ] P5A-01 — `AuditEventORM` model
- [ ] P5A-02 — `AuditRepository`
- [ ] P5A-03 — Rewrite `AuditService`
- [ ] P5A-04 — Alembic migration
- [ ] P5A-05 — Audit service tests

### 5B: Request Logging `[PENDING]`
- [ ] P5B-01 — Add timing to orchestrator
- [ ] P5B-02 — Persist request log
- [ ] P5B-03 — Handle blocked + error logging
- [ ] P5B-04 — Request logging tests

### 5C: Usage Records + Cost `[PENDING]`
- [ ] P5C-01 — `UsageRecordORM` model
- [ ] P5C-02 — `UsageRepository` with aggregation
- [ ] P5C-03 — Rewrite `UsageService`
- [ ] P5C-04 — Alembic migration
- [ ] P5C-05 — Usage tests

### 5D: Log Correlation `[PENDING]`
- [ ] P5D-01 — structlog contextvars setup
- [ ] P5D-02 — Bind tenant/project after auth
- [ ] P5D-03 — Verify context in all log lines

---

## Phase 6: Admin Console

### 6A: Admin Auth `[PENDING]`
- [ ] P6A-01 — `POST /admin/login`
- [ ] P6A-02 — Admin JWT middleware
- [ ] P6A-03 — Register in main.py
- [ ] P6A-04 — Admin UI login page
- [ ] P6A-05 — Admin UI auth utility
- [ ] P6A-06 — Auth tests

### 6B: Admin CRUD API `[PENDING]`
- [ ] P6B-01 — Expand admin schemas
- [ ] P6B-02 — Tenant CRUD
- [ ] P6B-03 — Project CRUD + API key generation
- [ ] P6B-04 — Provider CRUD
- [ ] P6B-05 — Model CRUD
- [ ] P6B-06 — Policy CRUD
- [ ] P6B-07 — Request log listing
- [ ] P6B-08 — Audit event listing
- [ ] P6B-09 — CRUD tests

### 6C: Admin UI Data Wiring `[PENDING]`
- [ ] P6C-01 — Typed API client
- [ ] P6C-02 — Providers page
- [ ] P6C-03 — Models page
- [ ] P6C-04 — Policies page
- [ ] P6C-05 — Requests page
- [ ] P6C-06 — Audit page
- [ ] P6C-07 — Usage page

### 6D: Admin UI Forms `[PENDING]`
- [ ] P6D-01 — Component library setup
- [ ] P6D-02 — Provider forms
- [ ] P6D-03 — Policy forms
- [ ] P6D-04 — Project forms + API key display
- [ ] P6D-05 — Delete confirmations
- [ ] P6D-06 — Toast notifications

---

## Phase 7: Enterprise Controls

### 7A: RBAC `[PENDING]`
- [ ] P7A-01 — Role-checking dependency
- [ ] P7A-02 — Apply to admin endpoints
- [ ] P7A-03 — RBAC tests

### 7B: Budget Caps `[PENDING]`
- [ ] P7B-01 — `get_current_spend()` query
- [ ] P7B-02 — Budget check in policy engine
- [ ] P7B-03 — Budget tests

### 7C: Rate Limiting `[PENDING]`
- [ ] P7C-01 — Redis connection module
- [ ] P7C-02 — `CacheService` with Redis
- [ ] P7C-03 — Sliding window rate limiter
- [ ] P7C-04 — Wire into orchestrator
- [ ] P7C-05 — Rate limit tests

### 7D: Alerting + Webhooks `[PENDING]`
- [ ] P7D-01 — Alert rule model + migration
- [ ] P7D-02 — Alert evaluation service
- [ ] P7D-03 — Webhook delivery
- [ ] P7D-04 — Wire into audit service
- [ ] P7D-05 — Alerting tests

---

## Phase 8: Scale / Compliance

### 8A: Response Caching `[PENDING]`
- [ ] P8A-01 — Cache key generation
- [ ] P8A-02 — Cache check before dispatch
- [ ] P8A-03 — Cache write after response
- [ ] P8A-04 — Cache bypass rules
- [ ] P8A-05 — Caching tests

### 8B: SSO/SAML `[PENDING]`
- [ ] P8B-01 — SAML 2.0 SP
- [ ] P8B-02 — OIDC client
- [ ] P8B-03 — Attribute-to-role mapping
- [ ] P8B-04 — SSO tests

### 8C: Immutable Audit `[PENDING]`
- [ ] P8C-01 — Hash chain on audit events
- [ ] P8C-02 — Integrity verification
- [ ] P8C-03 — Block update/delete
- [ ] P8C-04 — Integrity tests

### 8D: Compliance Packs `[PENDING]`
- [ ] P8D-01 — SOC2 template
- [ ] P8D-02 — HIPAA template + PHI
- [ ] P8D-03 — GDPR template + region
- [ ] P8D-04 — Pack loader
- [ ] P8D-05 — Compliance tests

---

## Summary

| Phase | Sub-phases | Tasks | Status |
|-------|-----------|-------|--------|
| 0: Foundation | 4 | 27 | NEXT |
| 1: Adapters | 6 | 28 | PENDING |
| 2: Auth | 4 | 18 | PENDING |
| 3: Policy/DLP | 4 | 17 | PENDING |
| 4: Routing | 4 | 16 | PENDING |
| 5: Observability | 4 | 16 | PENDING |
| 6: Admin | 4 | 28 | PENDING |
| 7: Enterprise | 4 | 16 | PENDING |
| 8: Scale | 4 | 17 | PENDING |
| **TOTAL** | **38** | **183** | — |
