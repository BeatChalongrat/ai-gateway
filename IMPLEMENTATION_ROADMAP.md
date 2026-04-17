# Secure AI Gateway — Implementation Roadmap

> Generated from deep audit on 2026-04-08.
> Workflow: **Planner → Builder → Auditor** (incremental, verifiable sub-phases).
> Each sub-phase is scoped so a Builder can execute it in isolation and an Auditor can verify it before proceeding.

---

## How to Use This Document

1. **Builder** picks the next sub-phase marked `[NEXT]` or the first `[PENDING]`.
2. Builder implements all tasks in that sub-phase, following the notes and test plan.
3. **Auditor** verifies acceptance criteria before the sub-phase is marked `[DONE]`.
4. If acceptance fails, Builder fixes. If it passes, move to the next sub-phase.
5. Never skip a sub-phase — each one builds on the previous.

**Status legend:** `[DONE]` `[IN PROGRESS]` `[NEXT]` `[PENDING]` `[BLOCKED]`

---

## Master Roadmap Overview

```
Phase 0: Foundation Hardening
  └─ 0A: Alembic + DB migrations .................. [NEXT]
  └─ 0B: Repository layer ......................... [PENDING]
  └─ 0C: Error handling + async conversion ........ [PENDING]
  └─ 0D: Configuration + environment hardening .... [PENDING]

Phase 1: Unified API + Provider Adapters + Orchestration
  └─ 1A: Provider response contract ............... [PENDING]
  └─ 1B: OpenAI adapter (real) .................... [PENDING]
  └─ 1C: Anthropic adapter (real) ................. [PENDING]
  └─ 1D: OpenRouter adapter (real) ................ [PENDING]
  └─ 1E: Orchestrator hardening + retry/timeout ... [PENDING]
  └─ 1F: Streaming (SSE) support .................. [PENDING]

Phase 2: Tenancy + Auth + Projects + API Keys
  └─ 2A: Tenant + API key data model .............. [PENDING]
  └─ 2B: API key hashing + validation ............. [PENDING]
  └─ 2C: Project resolution from DB ............... [PENDING]
  └─ 2D: Seed data + dev bootstrap ................ [PENDING]

Phase 3: Policy Engine + DLP/PII Masking
  └─ 3A: Policy profile data model ................ [PENDING]
  └─ 3B: Policy engine DB integration ............. [PENDING]
  └─ 3C: Expanded DLP patterns .................... [PENDING]
  └─ 3D: Response-side DLP scanning ............... [PENDING]

Phase 4: Routing Engine + Failover + Cost/Latency Awareness
  └─ 4A: Model registry data model ................ [PENDING]
  └─ 4B: Registry-based routing ................... [PENDING]
  └─ 4C: Fallback chains .......................... [PENDING]
  └─ 4D: Cost-aware routing ....................... [PENDING]

Phase 5: Observability + Audit Logs + Usage/Cost Tracking
  └─ 5A: Audit event persistence .................. [PENDING]
  └─ 5B: Request logging persistence .............. [PENDING]
  └─ 5C: Usage records + real cost calculation .... [PENDING]
  └─ 5D: Structured log correlation ............... [PENDING]

Phase 6: Admin Console Integration
  └─ 6A: Admin authentication ..................... [PENDING]
  └─ 6B: Admin CRUD API endpoints ................. [PENDING]
  └─ 6C: Admin UI — data wiring ................... [PENDING]
  └─ 6D: Admin UI — forms + actions ............... [PENDING]

Phase 7: Enterprise Controls
  └─ 7A: RBAC .................................... [PENDING]
  └─ 7B: Budget caps + quotas .................... [PENDING]
  └─ 7C: Rate limiting (Redis) ................... [PENDING]
  └─ 7D: Alerting + webhooks ..................... [PENDING]

Phase 8: Scale / Compliance Extensions
  └─ 8A: Response caching ........................ [PENDING]
  └─ 8B: SSO/SAML ................................ [PENDING]
  └─ 8C: Immutable audit log ..................... [PENDING]
  └─ 8D: Compliance packs ........................ [PENDING]
```

---

# Phase 0: Foundation Hardening

> **Goal:** Make the existing skeleton connect to a real database, handle errors gracefully, and run async. No new features — just make the scaffold production-safe.

---

## Sub-Phase 0A: Alembic + DB Migrations `[NEXT]`

**Objective:** Create the Alembic migration environment and generate the initial migration from the 3 existing ORM models so that `alembic upgrade head` produces real tables.

**Scope:**
- Create `backend/migrations/` directory structure (env.py, script.py.mako, versions/)
- Wire Alembic to the SQLAlchemy metadata from `core/db.py`
- Auto-generate initial migration from existing ORM models: `ProjectORM`, `ProviderORM`, `RequestLogORM`
- Add `created_at` / `updated_at` timestamp columns to all 3 models (missing from current definitions)

**Files touched:**
- `backend/alembic.ini` (update script_location)
- `backend/migrations/env.py` (NEW)
- `backend/migrations/script.py.mako` (NEW)
- `backend/migrations/versions/001_initial_schema.py` (NEW)
- `backend/app/models/project.py` (add timestamps)
- `backend/app/models/provider.py` (add timestamps)
- `backend/app/models/request_log.py` (add timestamps)
- `backend/app/models/__init__.py` (NEW — re-export all models for metadata discovery)

**Dependencies:** None — this is the first task.

**Implementation notes:**
- The existing `alembic.ini` points to `script_location = migrations` and uses PostgreSQL. Keep that.
- `env.py` must import `Base` from `app.core.db` and all ORM model modules so metadata is populated.
- Use `alembic revision --autogenerate` logic, but write it manually for control.
- Timestamps: use `Column(DateTime, server_default=func.now())` for `created_at`, `onupdate=func.now()` for `updated_at`.
- All string PKs should use `String(36)` for UUID compatibility.

**Test plan:**
1. `alembic upgrade head` runs without error against a clean PostgreSQL.
2. All 3 tables exist with correct columns and types.
3. `alembic downgrade base` drops all tables cleanly.
4. `alembic upgrade head` is idempotent (running twice doesn't error).

**Acceptance criteria:**
- [ ] `backend/migrations/env.py` exists and imports all models
- [ ] `backend/migrations/versions/` has one migration file
- [ ] `alembic upgrade head` creates `projects`, `providers`, `request_logs` tables
- [ ] All tables have `created_at` and `updated_at` columns
- [ ] `alembic downgrade base` reverses cleanly
- [ ] No import errors when running the migration

**Rollback/risk:**
- Low risk. Only creates new files and adds columns to models.
- If models are wrong, delete the migration file and regenerate.

### Task Board — Sub-Phase 0A

| ID | Task | Est. |
|----|------|------|
| P0A-01 | Create `backend/migrations/env.py` with async-compatible Alembic config | S |
| P0A-02 | Create `backend/migrations/script.py.mako` template | XS |
| P0A-03 | Create `backend/app/models/__init__.py` that imports all ORM models | XS |
| P0A-04 | Add `created_at` + `updated_at` to `ProjectORM`, `ProviderORM`, `RequestLogORM` | S |
| P0A-05 | Update `alembic.ini` — verify `script_location` and connection string | XS |
| P0A-06 | Generate initial migration `001_initial_schema.py` | M |
| P0A-07 | Test: run `alembic upgrade head` + `downgrade base` against Docker PostgreSQL | S |

---

## Sub-Phase 0B: Repository Layer `[PENDING]`

**Objective:** Implement the 3 existing repository stubs so services can read/write to the database.

**Scope:**
- Implement `BaseRepository` with session management pattern
- Implement `ProjectRepository` (CRUD + lookup by api_key)
- Implement `ProviderRepository` (CRUD + list enabled)
- Implement `RequestLogRepository` (create + paginated list)
- Add FastAPI dependency for DB session injection

**Files touched:**
- `backend/app/repositories/base.py` (implement)
- `backend/app/repositories/project_repository.py` (implement)
- `backend/app/repositories/provider_repository.py` (implement)
- `backend/app/repositories/request_log_repository.py` (implement)
- `backend/app/api/deps.py` (add `get_db` session dependency)
- `backend/tests/test_repositories.py` (NEW)

**Dependencies:** Sub-Phase 0A (tables must exist)

**Implementation notes:**
- `BaseRepository` takes a `Session` in constructor. Each method operates within that session.
- Repositories return domain entities (from `domain/entities.py`), not ORM models. Conversion happens in the repository.
- Use `Session` from `sqlalchemy.orm`, not async session yet (convert in 0C).
- `get_db` dependency yields a session and ensures cleanup via `finally`.
- For `list` operations, accept `limit` + `offset` parameters.
- `ProjectRepository.get_by_api_key(hashed_key)` won't work yet (no api_keys table). Add a TODO and a `get_by_id(project_id)` for now.

**Test plan:**
1. Test each repository method against a test database (SQLite in-memory or test PostgreSQL).
2. Test CRUD cycle: create → read → update → delete.
3. Test list pagination.
4. Test not-found cases return `None`.

**Acceptance criteria:**
- [ ] `BaseRepository` provides session management
- [ ] All 3 repositories implement CRUD operations
- [ ] Repositories return domain entities, not ORM models
- [ ] `get_db` dependency yields sessions correctly
- [ ] All repository tests pass
- [ ] No raw SQL — all queries via SQLAlchemy ORM

**Rollback/risk:**
- Low risk. Only implements existing empty classes.
- If schema changes later, repositories adapt — domain entities are the contract.

### Task Board — Sub-Phase 0B

| ID | Task | Est. |
|----|------|------|
| P0B-01 | Implement `BaseRepository` with session pattern + common CRUD helpers | S |
| P0B-02 | Implement `ProjectRepository` — create, get_by_id, list_by_tenant, update | M |
| P0B-03 | Implement `ProviderRepository` — create, get_by_name, list_enabled, update | M |
| P0B-04 | Implement `RequestLogRepository` — create, list_by_project, list_by_tenant | M |
| P0B-05 | Add `get_db` session dependency to `api/deps.py` | S |
| P0B-06 | Write repository unit tests with test DB fixture | M |
| P0B-07 | Create `backend/tests/conftest.py` with test DB setup/teardown | M |

---

## Sub-Phase 0C: Error Handling + Async Conversion `[PENDING]`

**Objective:** Add structured error handling throughout the gateway and convert the orchestrator + adapters to async so provider HTTP calls won't block.

**Scope:**
- Define custom exception hierarchy
- Add global exception handler middleware
- Convert orchestrator to `async def`
- Convert provider adapter base and stubs to `async def`
- Add structured error responses using `ErrorResponse` schema

**Files touched:**
- `backend/app/core/exceptions.py` (NEW)
- `backend/app/middleware/error_handler.py` (NEW)
- `backend/app/main.py` (register error handler)
- `backend/app/services/orchestrator.py` (async conversion + try/except per step)
- `backend/app/services/providers/base.py` (async)
- `backend/app/services/providers/openai_adapter.py` (async)
- `backend/app/services/providers/anthropic_adapter.py` (async)
- `backend/app/services/providers/openrouter_adapter.py` (async)
- `backend/app/services/provider_dispatcher.py` (async)
- `backend/app/api/routes/public_chat.py` (async route handler)
- `backend/tests/test_error_handling.py` (NEW)

**Dependencies:** None (can run parallel with 0B if careful)

**Implementation notes:**
- Exception hierarchy:
  ```
  GatewayError (base)
  ├── AuthenticationError (401)
  ├── AuthorizationError (403)
  ├── PolicyBlockedError (403)
  ├── ValidationError (422)
  ├── RateLimitError (429)
  ├── ProviderError (502)
  ├── ProviderTimeoutError (504)
  └── InternalError (500)
  ```
- Global handler catches `GatewayError` subclasses and returns `ErrorResponse` JSON with correct HTTP status.
- Unhandled exceptions → 500 with generic message (never expose stack traces).
- Orchestrator: wrap each pipeline step in try/except. Catch specific exceptions, re-raise as appropriate `GatewayError`.
- All adapters become `async def chat(...)`. Stubs can just `return` the same strings for now.

**Test plan:**
1. Trigger each error type and verify correct HTTP status + JSON body.
2. Verify no stack traces in error responses.
3. Verify orchestrator still works end-to-end (regression).
4. Verify async stubs still return correct mock data.

**Acceptance criteria:**
- [ ] `GatewayError` hierarchy defined in `core/exceptions.py`
- [ ] Global error handler registered and catches all `GatewayError` types
- [ ] Unknown exceptions return 500 with safe message
- [ ] Orchestrator is fully async
- [ ] All provider adapters are async
- [ ] Chat endpoint still returns 200 with valid request
- [ ] Error handling tests pass

**Rollback/risk:**
- Medium risk. Async conversion touches the core pipeline.
- Test the happy path thoroughly after conversion.
- If async breaks something, the sync versions are in git history.

### Task Board — Sub-Phase 0C

| ID | Task | Est. |
|----|------|------|
| P0C-01 | Create `core/exceptions.py` with full exception hierarchy | S |
| P0C-02 | Create `middleware/error_handler.py` global exception handler | M |
| P0C-03 | Register error handler in `main.py` | XS |
| P0C-04 | Convert orchestrator to async with try/except per step | M |
| P0C-05 | Convert all provider adapters + dispatcher to async | S |
| P0C-06 | Convert `public_chat.py` route to async | XS |
| P0C-07 | Write error handling tests (each error type → correct HTTP status) | M |
| P0C-08 | Regression test: happy-path chat still works | S |

---

## Sub-Phase 0D: Configuration + Environment Hardening `[PENDING]`

**Objective:** Make configuration production-safe. Validate required settings, add missing config keys, set up `.env` properly.

**Scope:**
- Add all missing config fields to `Settings` class
- Add validation (required fields, format checks)
- Add environment-specific defaults (dev vs prod)
- Update `.env.example` with all config keys
- Add deep health check (DB + Redis connectivity)

**Files touched:**
- `backend/app/core/config.py` (expand)
- `.env.example` (update)
- `infra/docker/backend.env.example` (update)
- `backend/app/api/routes/health.py` (add deep health check)
- `backend/tests/test_config.py` (NEW)

**Dependencies:** Sub-Phase 0A (DB connection needed for health check)

**Implementation notes:**
- New config fields needed:
  - `CORS_ALLOWED_ORIGINS` (comma-separated list)
  - `LOG_LEVEL` (default: INFO)
  - `PROVIDER_DEFAULT_TIMEOUT_SECONDS` (default: 30)
  - `MAX_REQUEST_SIZE_BYTES` (default: 1048576)
  - `ENVIRONMENT` (dev/staging/prod)
  - `OPENAI_API_KEY` (optional)
  - `ANTHROPIC_API_KEY` (optional)
  - `OPENROUTER_API_KEY` (optional)
- Add Pydantic `field_validator` for critical fields.
- Health check: `GET /health` → basic. `GET /health/deep` → checks DB + Redis.

**Test plan:**
1. App starts with minimal valid `.env`.
2. App fails fast with clear error if `DATABASE_URL` missing.
3. `/health` returns quickly.
4. `/health/deep` correctly reports DB up/down.

**Acceptance criteria:**
- [ ] All config fields documented in `.env.example`
- [ ] Missing required config → clear startup error
- [ ] `/health/deep` checks DB and Redis connectivity
- [ ] CORS origins configurable via env
- [ ] Provider API keys loadable from env

**Rollback/risk:**
- Low risk. Configuration changes are additive.

### Task Board — Sub-Phase 0D

| ID | Task | Est. |
|----|------|------|
| P0D-01 | Expand `Settings` class with all missing config fields + validators | M |
| P0D-02 | Update `.env.example` and `backend.env.example` | S |
| P0D-03 | Add `GET /health/deep` with DB + Redis checks | M |
| P0D-04 | Add CORS middleware configuration from settings | S |
| P0D-05 | Write config validation tests | S |

---

# Phase 1: Unified API + Provider Adapters + Orchestration

> **Goal:** The gateway can actually proxy requests to real LLM providers, handle their responses, normalize errors, support streaming, and retry on failure.

---

## Sub-Phase 1A: Provider Response Contract `[PENDING]`

**Objective:** Define the normalized response dataclass that all adapters must return, so the orchestrator has a stable contract.

**Scope:**
- Create `ProviderResponse` dataclass
- Update orchestrator to use it
- Update dispatcher to enforce it

**Files touched:**
- `backend/app/domain/entities.py` (add `ProviderResponse`)
- `backend/app/services/providers/base.py` (update return type)
- `backend/app/services/provider_dispatcher.py` (update)
- `backend/app/services/orchestrator.py` (use `ProviderResponse` fields)

**Dependencies:** Sub-Phase 0C (async orchestrator)

**Implementation notes:**
- `ProviderResponse` fields:
  ```python
  @dataclass
  class ProviderResponse:
      content: str
      model: str                    # actual model used (may differ from requested)
      provider: str
      prompt_tokens: int | None     # provider-reported, if available
      completion_tokens: int | None
      finish_reason: str            # "stop", "length", "tool_calls", etc.
      raw_response: dict | None     # for debugging, never exposed to client
  ```
- Adapter stubs return `ProviderResponse` with mock values.
- Orchestrator uses `provider_response.prompt_tokens` for usage (fallback to estimation if None).

**Test plan:**
1. Chat endpoint still returns 200.
2. Response includes correct `model` and `provider` fields from `ProviderResponse`.

**Acceptance criteria:**
- [ ] `ProviderResponse` dataclass defined
- [ ] All 3 adapter stubs return `ProviderResponse`
- [ ] Orchestrator uses `ProviderResponse` for response formatting
- [ ] Existing tests still pass

### Task Board — Sub-Phase 1A

| ID | Task | Est. |
|----|------|------|
| P1A-01 | Define `ProviderResponse` dataclass in `domain/entities.py` | S |
| P1A-02 | Update `BaseProviderAdapter.chat()` return type to `ProviderResponse` | XS |
| P1A-03 | Update all 3 adapter stubs to return `ProviderResponse` | S |
| P1A-04 | Update orchestrator to consume `ProviderResponse` fields | M |
| P1A-05 | Update dispatcher to type-check `ProviderResponse` return | XS |

---

## Sub-Phase 1B: OpenAI Adapter (Real) `[PENDING]`

**Objective:** Replace the OpenAI stub with real API calls using the OpenAI Python SDK.

**Scope:**
- Install `openai` SDK
- Implement real `chat()` with request/response mapping
- Handle OpenAI-specific errors
- Load API key from config

**Files touched:**
- `backend/requirements.txt` (add `openai`)
- `backend/app/services/providers/openai_adapter.py` (full rewrite)
- `backend/tests/test_openai_adapter.py` (NEW)

**Dependencies:** Sub-Phase 1A (response contract), Sub-Phase 0D (API key config)

**Implementation notes:**
- Use `openai.AsyncOpenAI` client.
- Map `ChatCompletionsRequest.messages` to OpenAI format (already compatible).
- Map OpenAI response to `ProviderResponse`.
- Handle errors:
  - `openai.RateLimitError` → `ProviderError` with retry hint
  - `openai.APITimeoutError` → `ProviderTimeoutError`
  - `openai.AuthenticationError` → log + `ProviderError`
  - `openai.BadRequestError` → `ValidationError`
- Use `httpx.AsyncClient` timeout from config.
- **Do NOT hardcode the API key.** Read from `settings.OPENAI_API_KEY`.

**Test plan:**
1. Mock test: mock OpenAI client → verify request mapping + response mapping.
2. Mock test: mock OpenAI error → verify correct `GatewayError` raised.
3. Integration test (optional, requires real key): send real request and verify response.

**Acceptance criteria:**
- [ ] `openai` in `requirements.txt`
- [ ] Adapter uses `openai.AsyncOpenAI`
- [ ] Request mapped correctly to OpenAI format
- [ ] Response mapped to `ProviderResponse` with real token counts
- [ ] All OpenAI error types handled
- [ ] API key loaded from config, never hardcoded
- [ ] Mock tests pass

### Task Board — Sub-Phase 1B

| ID | Task | Est. |
|----|------|------|
| P1B-01 | Add `openai` to `requirements.txt` | XS |
| P1B-02 | Implement `OpenAIAdapter.chat()` with real SDK calls | L |
| P1B-03 | Implement error mapping (OpenAI errors → GatewayErrors) | M |
| P1B-04 | Write mock tests for request/response mapping | M |
| P1B-05 | Write mock tests for error scenarios | M |

---

## Sub-Phase 1C: Anthropic Adapter (Real) `[PENDING]`

**Objective:** Replace the Anthropic stub with real API calls using the Anthropic Python SDK.

**Scope:** Same as 1B but for Anthropic.

**Files touched:**
- `backend/requirements.txt` (add `anthropic`)
- `backend/app/services/providers/anthropic_adapter.py` (full rewrite)
- `backend/tests/test_anthropic_adapter.py` (NEW)

**Dependencies:** Sub-Phase 1A

**Implementation notes:**
- Use `anthropic.AsyncAnthropic` client.
- **Key difference from OpenAI:** Anthropic treats `system` messages differently. Extract system message from `messages` list and pass as `system` parameter.
- Map Anthropic `content[0].text` response format to `ProviderResponse.content`.
- Handle `anthropic.RateLimitError`, `anthropic.APITimeoutError`, etc.

**Test plan:** Same structure as 1B.

**Acceptance criteria:**
- [ ] `anthropic` in `requirements.txt`
- [ ] System message extraction handled correctly
- [ ] Response mapped to `ProviderResponse`
- [ ] All error types handled
- [ ] Mock tests pass

### Task Board — Sub-Phase 1C

| ID | Task | Est. |
|----|------|------|
| P1C-01 | Add `anthropic` to `requirements.txt` | XS |
| P1C-02 | Implement `AnthropicAdapter.chat()` with real SDK calls | L |
| P1C-03 | Implement system message extraction logic | S |
| P1C-04 | Implement error mapping | M |
| P1C-05 | Write mock tests | M |

---

## Sub-Phase 1D: OpenRouter Adapter (Real) `[PENDING]`

**Objective:** Replace the OpenRouter stub with real HTTP calls (OpenAI-compatible API).

**Scope:** Similar to 1B but using raw `httpx` since OpenRouter is OpenAI-compatible.

**Files touched:**
- `backend/requirements.txt` (add `httpx` if not present)
- `backend/app/services/providers/openrouter_adapter.py` (full rewrite)
- `backend/tests/test_openrouter_adapter.py` (NEW)

**Dependencies:** Sub-Phase 1A

**Implementation notes:**
- Use `httpx.AsyncClient` directly (no SDK).
- OpenRouter endpoint: `https://openrouter.ai/api/v1/chat/completions`
- Required headers: `Authorization: Bearer <key>`, `HTTP-Referer`, `X-Title`.
- Response format is OpenAI-compatible — reuse mapping logic.

**Test plan:** Same structure as 1B.

**Acceptance criteria:**
- [ ] Adapter uses `httpx.AsyncClient`
- [ ] OpenRouter-specific headers included
- [ ] Response mapped to `ProviderResponse`
- [ ] Mock tests pass

### Task Board — Sub-Phase 1D

| ID | Task | Est. |
|----|------|------|
| P1D-01 | Implement `OpenRouterAdapter.chat()` with httpx | L |
| P1D-02 | Implement error mapping | M |
| P1D-03 | Write mock tests | M |

---

## Sub-Phase 1E: Orchestrator Hardening — Retry + Timeout `[PENDING]`

**Objective:** Add per-provider timeout enforcement and retry with fallback to the orchestrator dispatch step.

**Scope:**
- Add timeout wrapper around provider dispatch
- Add retry logic (configurable max retries)
- Add fallback provider selection on failure

**Files touched:**
- `backend/app/services/orchestrator.py` (add retry/timeout logic)
- `backend/app/services/routing_service.py` (add `select_fallback_route()`)
- `backend/app/core/config.py` (add retry config)
- `backend/tests/test_orchestrator_retry.py` (NEW)

**Dependencies:** Sub-Phase 1B (at least one real adapter)

**Implementation notes:**
- Use `asyncio.wait_for(dispatch(), timeout=settings.PROVIDER_DEFAULT_TIMEOUT_SECONDS)`.
- Retry strategy: on `ProviderError` or `ProviderTimeoutError`, retry up to `MAX_RETRIES` times with exponential backoff (1s, 2s, 4s).
- If all retries fail, try fallback route (different provider for same capability).
- If fallback also fails, raise `ProviderError` with aggregated error info.

**Test plan:**
1. Mock adapter that fails once then succeeds → verify retry works.
2. Mock adapter that always fails → verify fallback is attempted.
3. All retries + fallback fail → verify clean error response.
4. Timeout → verify `ProviderTimeoutError` raised.

**Acceptance criteria:**
- [ ] Provider calls have configurable timeout
- [ ] Retry with exponential backoff on provider failure
- [ ] Fallback routing attempted after retry exhaustion
- [ ] All failure paths produce clean error responses
- [ ] Tests cover retry, fallback, and total failure scenarios

### Task Board — Sub-Phase 1E

| ID | Task | Est. |
|----|------|------|
| P1E-01 | Add timeout wrapper to provider dispatch | S |
| P1E-02 | Implement retry with exponential backoff | M |
| P1E-03 | Implement `select_fallback_route()` in routing service | M |
| P1E-04 | Wire retry + fallback into orchestrator | M |
| P1E-05 | Write retry/fallback tests | L |

---

## Sub-Phase 1F: Streaming (SSE) Support `[PENDING]`

**Objective:** Support `stream: true` in chat completions with Server-Sent Events.

**Scope:**
- Add streaming path in orchestrator
- Implement SSE streaming in OpenAI adapter
- Implement SSE streaming in Anthropic adapter
- Return `StreamingResponse` from FastAPI

**Files touched:**
- `backend/app/services/orchestrator.py` (add streaming path)
- `backend/app/services/providers/openai_adapter.py` (add `chat_stream()`)
- `backend/app/services/providers/anthropic_adapter.py` (add `chat_stream()`)
- `backend/app/services/providers/base.py` (add abstract `chat_stream()`)
- `backend/app/api/routes/public_chat.py` (return `StreamingResponse`)
- `backend/tests/test_streaming.py` (NEW)

**Dependencies:** Sub-Phase 1B + 1C (real adapters)

**Implementation notes:**
- Streaming path: DLP scan input → policy check → route → stream from provider → return SSE.
- Response-side DLP on streamed content is deferred (accumulate and scan post-stream in Phase 3D).
- Use `async def stream_generator()` that yields SSE `data:` lines.
- Final chunk: `data: [DONE]\n\n` (OpenAI convention).
- Usage data available only after stream completes — emit as final SSE event or skip.

**Test plan:**
1. `stream: true` returns `text/event-stream` content type.
2. SSE chunks are well-formed.
3. Final `[DONE]` event received.
4. Non-streaming still works (regression).

**Acceptance criteria:**
- [ ] `stream: true` returns `StreamingResponse` with SSE
- [ ] OpenAI streaming works with real API
- [ ] Anthropic streaming works with real API
- [ ] SSE format matches OpenAI convention
- [ ] Non-streaming regression test passes

### Task Board — Sub-Phase 1F

| ID | Task | Est. |
|----|------|------|
| P1F-01 | Add `chat_stream()` to `BaseProviderAdapter` | XS |
| P1F-02 | Implement OpenAI streaming in adapter | L |
| P1F-03 | Implement Anthropic streaming in adapter | L |
| P1F-04 | Add streaming path to orchestrator | M |
| P1F-05 | Return `StreamingResponse` from chat route | M |
| P1F-06 | Write streaming tests | M |

---

# Phase 2: Tenancy + Auth + Projects + API Keys

> **Goal:** Multi-tenant data model with real API key authentication. Projects resolved from DB, not hardcoded.

---

## Sub-Phase 2A: Tenant + API Key Data Model `[PENDING]`

**Objective:** Create the ORM models and migrations for tenants, users, and API keys.

**Scope:**
- `TenantORM` (tenant_id, name, slug, region, status, created_at, updated_at)
- `UserORM` (user_id, tenant_id FK, email, password_hash, role, created_at)
- `ApiKeyORM` (key_id, hashed_key, key_prefix, project_id FK, name, created_at, expires_at, revoked_at)
- Alembic migration for all 3 new tables
- Add foreign key from `ProjectORM.tenant_id` → `TenantORM.tenant_id`

**Files touched:**
- `backend/app/models/tenant.py` (NEW)
- `backend/app/models/user.py` (NEW)
- `backend/app/models/api_key.py` (NEW)
- `backend/app/models/__init__.py` (register new models)
- `backend/app/models/project.py` (add FK constraint)
- `backend/migrations/versions/002_tenants_users_apikeys.py` (NEW)

**Dependencies:** Sub-Phase 0A

**Implementation notes:**
- `ApiKeyORM.hashed_key` is SHA-256 of the raw key. The raw key is shown once at creation, never stored.
- `ApiKeyORM.key_prefix` stores first 8 chars of the raw key for identification (e.g., `sk-gw-ab12...`).
- `UserORM.role` is an enum: `admin`, `viewer`, `operator`.
- FK cascade: deleting a tenant cascades to projects; deleting a project cascades to API keys.

**Test plan:**
1. Migration runs clean.
2. FK constraints enforced (can't create project without tenant).
3. Cascade delete works.

**Acceptance criteria:**
- [ ] 3 new tables created via migration
- [ ] FK constraints enforced
- [ ] API key model has `hashed_key` and `key_prefix`
- [ ] User model has `role` enum
- [ ] Migration is reversible

### Task Board — Sub-Phase 2A

| ID | Task | Est. |
|----|------|------|
| P2A-01 | Create `TenantORM` model | S |
| P2A-02 | Create `UserORM` model with role enum | S |
| P2A-03 | Create `ApiKeyORM` model with hash + prefix | M |
| P2A-04 | Add FK from `ProjectORM` to `TenantORM` | S |
| P2A-05 | Generate Alembic migration | M |
| P2A-06 | Test migration up + down | S |

---

## Sub-Phase 2B: API Key Hashing + Validation `[PENDING]`

**Objective:** Implement real API key hashing and validation against the database.

**Scope:**
- Implement `hash_api_key()` with SHA-256
- Implement `ApiKeyRepository` with lookup by hash
- Create API key generation utility (for admin/seed use)
- Wire validation into the `get_project_api_key` dependency

**Files touched:**
- `backend/app/core/security.py` (implement hash_api_key)
- `backend/app/repositories/api_key_repository.py` (NEW)
- `backend/app/core/api_key_utils.py` (NEW — generation utility)
- `backend/app/api/deps.py` (wire validation)
- `backend/tests/test_auth.py` (NEW)

**Dependencies:** Sub-Phase 2A (api_keys table), Sub-Phase 0B (repository pattern)

**Implementation notes:**
- Key format: `sk-gw-<32 random hex chars>` (total 38 chars).
- Hash: `SHA-256(raw_key)` stored in `hashed_key` column.
- Lookup flow: `hash(bearer_token)` → query `api_keys` by `hashed_key` → join project → join tenant.
- Check `revoked_at IS NULL` and `expires_at > now()`.
- Return 401 if key not found. Return 403 if revoked or expired.
- **Never log the raw API key.** Log only the prefix.

**Test plan:**
1. Valid key → project context returned.
2. Invalid key → 401.
3. Revoked key → 403.
4. Expired key → 403.
5. Key prefix logged, not full key.

**Acceptance criteria:**
- [ ] `hash_api_key()` uses SHA-256
- [ ] API key generation produces correct format
- [ ] Lookup by hash works
- [ ] Revoked/expired keys rejected
- [ ] Raw keys never logged

### Task Board — Sub-Phase 2B

| ID | Task | Est. |
|----|------|------|
| P2B-01 | Implement `hash_api_key()` with SHA-256 | S |
| P2B-02 | Create `api_key_utils.py` — key generation + hashing | M |
| P2B-03 | Implement `ApiKeyRepository` with lookup by hash + join project/tenant | M |
| P2B-04 | Wire key validation into `get_project_api_key` dependency | M |
| P2B-05 | Write auth tests (valid, invalid, revoked, expired) | M |

---

## Sub-Phase 2C: Project Resolution from DB `[PENDING]`

**Objective:** Replace the hardcoded project context in `ProjectService` with real DB lookups.

**Scope:**
- Rewrite `ProjectService.resolve_project_from_api_key()`
- Wire DB session through orchestrator
- Ensure tenant isolation on all queries

**Files touched:**
- `backend/app/services/project_service.py` (full rewrite)
- `backend/app/services/orchestrator.py` (accept DB session)
- `backend/app/api/routes/public_chat.py` (inject session)
- `backend/tests/test_project_service.py` (NEW)

**Dependencies:** Sub-Phase 2B (API key validation)

**Implementation notes:**
- `resolve_project_from_api_key(api_key, db)` → hash key → lookup → return `Project` entity with tenant context.
- Orchestrator receives `db: Session` (or `AsyncSession`) via dependency injection.
- All downstream services that need DB access receive the session.

**Test plan:**
1. Valid key → correct project + tenant returned.
2. Project context includes `policy_profile_id` and `budget_limit`.
3. Cross-tenant access impossible (can't use tenant A's key to access tenant B's project).

**Acceptance criteria:**
- [ ] `ProjectService` reads from DB, not hardcoded
- [ ] Correct project entity returned with all fields
- [ ] Tenant context populated
- [ ] Cross-tenant isolation enforced

### Task Board — Sub-Phase 2C

| ID | Task | Est. |
|----|------|------|
| P2C-01 | Rewrite `ProjectService.resolve_project_from_api_key()` to use DB | M |
| P2C-02 | Inject DB session into orchestrator call chain | M |
| P2C-03 | Write project resolution tests | M |

---

## Sub-Phase 2D: Seed Data + Dev Bootstrap `[PENDING]`

**Objective:** Create a seed script that populates a demo tenant, project, API key, and providers so the gateway works out of the box for development.

**Scope:**
- Implement `seed_demo_data.py`
- Update `dev_bootstrap.sh` to run migrations + seed
- Print the demo API key to console on seed

**Files touched:**
- `backend/scripts/seed_demo_data.py` (full rewrite)
- `scripts/dev_bootstrap.sh` (update)
- `Makefile` (add `seed` target)

**Dependencies:** Sub-Phase 2A + 2B (tables + key generation)

**Implementation notes:**
- Create: 1 tenant ("Demo Corp"), 1 project ("Default Project"), 1 API key, 3 providers (OpenAI, Anthropic, OpenRouter).
- Print the raw API key to console: "Your demo API key: sk-gw-xxxx... (save this, it won't be shown again)".
- Make seed idempotent: check if demo tenant exists before creating.

**Test plan:**
1. Run seed on empty DB → all records created.
2. Run seed again → no duplicates, no errors.
3. Use printed API key to call chat endpoint → success.

**Acceptance criteria:**
- [ ] Seed creates demo tenant, project, API key, providers
- [ ] Seed is idempotent
- [ ] Printed API key works for authentication
- [ ] `make seed` runs the script

### Task Board — Sub-Phase 2D

| ID | Task | Est. |
|----|------|------|
| P2D-01 | Implement `seed_demo_data.py` | M |
| P2D-02 | Make seed idempotent | S |
| P2D-03 | Update `dev_bootstrap.sh` and `Makefile` | S |
| P2D-04 | Test end-to-end: seed → authenticate → chat | M |

---

# Phase 3: Policy Engine + DLP/PII Masking

> **Goal:** Configurable policy profiles in the database. Expanded DLP detection. Response scanning.

---

## Sub-Phase 3A: Policy Profile Data Model `[PENDING]`

**Objective:** Create the policy profiles table and ORM model.

**Files touched:**
- `backend/app/models/policy_profile.py` (NEW)
- `backend/app/models/__init__.py` (register)
- `backend/app/repositories/policy_repository.py` (NEW)
- `backend/migrations/versions/003_policy_profiles.py` (NEW)

**Dependencies:** Sub-Phase 0A

**Implementation notes:**
- `PolicyProfileORM`: profile_id, tenant_id FK, name, mode (enforce/audit/disabled), rules (JSON column), created_at, updated_at.
- Rules JSON schema matches `sample_policy_profile.json`:
  ```json
  {
    "allowed_models": ["gpt-4o-mini", "claude-3-5-sonnet"],
    "blocked_data_types": ["credit_card", "bank_account", "secrets"],
    "pii_action": "mask",
    "region_constraints": ["us", "eu"],
    "max_tokens": 4096
  }
  ```
- Repository: CRUD + `get_by_id` used by policy engine.

**Acceptance criteria:**
- [ ] Table created via migration
- [ ] JSON rules column stores structured policy config
- [ ] Repository CRUD works
- [ ] Tests pass

### Task Board — Sub-Phase 3A

| ID | Task | Est. |
|----|------|------|
| P3A-01 | Create `PolicyProfileORM` model | M |
| P3A-02 | Create `PolicyProfileRepository` with CRUD | M |
| P3A-03 | Generate Alembic migration | S |
| P3A-04 | Write repository tests | M |

---

## Sub-Phase 3B: Policy Engine DB Integration `[PENDING]`

**Objective:** Policy engine loads the active profile from DB and evaluates all configured rules.

**Files touched:**
- `backend/app/services/policy_engine.py` (rewrite)
- `backend/tests/test_policy_engine.py` (NEW)

**Dependencies:** Sub-Phase 3A, Sub-Phase 2C (project has policy_profile_id)

**Implementation notes:**
- Load profile by `project.policy_profile_id`.
- Evaluate each rule from the JSON `rules` field:
  - `allowed_models`: check if requested model is in list.
  - `blocked_data_types`: check DLP findings against blocked types.
  - `pii_action`: "mask" or "block" based on DLP findings.
  - `max_tokens`: check request `max_tokens` parameter.
- Mode:
  - `enforce`: block/mask as configured.
  - `audit`: allow but log what would have been blocked.
  - `disabled`: skip evaluation.
- Return `PolicyDecision` with all reasons.

**Acceptance criteria:**
- [ ] Policy loaded from DB by profile_id
- [ ] Each rule type evaluated
- [ ] Mode (enforce/audit/disabled) respected
- [ ] Reasons list includes all evaluated rules
- [ ] Tests cover each rule type

### Task Board — Sub-Phase 3B

| ID | Task | Est. |
|----|------|------|
| P3B-01 | Rewrite `PolicyEngine.evaluate()` to load profile from DB | L |
| P3B-02 | Implement each rule evaluator (allowed_models, blocked_data_types, pii_action, max_tokens) | L |
| P3B-03 | Implement mode logic (enforce, audit, disabled) | M |
| P3B-04 | Write policy engine tests for each rule type + mode | L |

---

## Sub-Phase 3C: Expanded DLP Patterns `[PENDING]`

**Objective:** Add detection for SSN, credit card, AWS keys, generic high-entropy secrets.

**Files touched:**
- `backend/app/services/dlp_service.py` (expand)
- `backend/tests/test_dlp_service.py` (NEW)

**Dependencies:** None (can start anytime)

**Implementation notes:**
- SSN: `\b\d{3}-\d{2}-\d{4}\b`
- Credit card: Luhn algorithm validation on 13-19 digit sequences
- AWS keys: `AKIA[A-Z0-9]{16}`
- Generic secrets: `(?:password|secret|token|api.?key)\s*[:=]\s*\S+` (case-insensitive)
- Each pattern returns a finding type (e.g., `"ssn"`, `"credit_card"`, `"aws_key"`, `"secret"`)
- Findings include position info for masking.

**Acceptance criteria:**
- [ ] SSN detected and masked
- [ ] Credit card numbers validated with Luhn
- [ ] AWS access keys detected
- [ ] High-entropy secrets detected
- [ ] Each pattern has positive and negative test cases

### Task Board — Sub-Phase 3C

| ID | Task | Est. |
|----|------|------|
| P3C-01 | Add SSN regex pattern | S |
| P3C-02 | Add credit card detection with Luhn validation | M |
| P3C-03 | Add AWS key detection | S |
| P3C-04 | Add generic secret detection | M |
| P3C-05 | Write comprehensive DLP test suite (positive + negative) | L |

---

## Sub-Phase 3D: Response-Side DLP Scanning `[PENDING]`

**Objective:** Scan provider responses for PII/secrets before returning to client.

**Files touched:**
- `backend/app/services/orchestrator.py` (add response scanning step)
- `backend/app/services/dlp_service.py` (add `scan_response()` method)
- `backend/tests/test_response_scanning.py` (NEW)

**Dependencies:** Sub-Phase 3C (expanded patterns)

**Implementation notes:**
- After provider dispatch, before response formatting: `dlp_service.scan_response(response_content)`.
- If findings: mask in response content. Add to audit metadata.
- For streaming (Phase 1F): accumulate chunks, scan at end, log findings. (Masking mid-stream is complex — defer to Phase 7.)

**Acceptance criteria:**
- [ ] Provider response scanned before return
- [ ] PII in response is masked
- [ ] Audit metadata includes response DLP findings
- [ ] Non-streaming happy path works

### Task Board — Sub-Phase 3D

| ID | Task | Est. |
|----|------|------|
| P3D-01 | Add `scan_response()` to DLP service | M |
| P3D-02 | Wire response scanning into orchestrator (after dispatch, before format) | M |
| P3D-03 | Log response DLP findings to audit metadata | S |
| P3D-04 | Write response scanning tests | M |

---

# Phase 4: Routing Engine + Failover + Cost/Latency Awareness

> **Goal:** Routing decisions based on a real model registry. Cost-aware auto-routing. Fallback chains.

---

## Sub-Phase 4A: Model Registry Data Model `[PENDING]`

**Objective:** Create a models table with capabilities, pricing, and constraints.

**Files touched:**
- `backend/app/models/model_registry.py` (NEW)
- `backend/app/repositories/model_repository.py` (NEW)
- `backend/migrations/versions/004_model_registry.py` (NEW)

**Dependencies:** Sub-Phase 0A

**Implementation notes:**
- `ModelRegistryORM`: model_id, provider_id FK, model_name, display_name, context_window, supports_streaming, supports_tools, supports_json_mode, cost_per_input_token (Decimal), cost_per_output_token (Decimal), risk_level, regions (JSON array), enabled, created_at.
- Seed with common models: gpt-4o, gpt-4o-mini, claude-3-5-sonnet, claude-3-haiku, etc.

**Acceptance criteria:**
- [ ] Table created with all fields
- [ ] Pricing uses Decimal (not float) for accuracy
- [ ] Repository CRUD + `list_by_provider` + `list_enabled` works
- [ ] Seed includes at least 6 common models with real pricing

### Task Board — Sub-Phase 4A

| ID | Task | Est. |
|----|------|------|
| P4A-01 | Create `ModelRegistryORM` model | M |
| P4A-02 | Create `ModelRepository` with CRUD + filters | M |
| P4A-03 | Generate Alembic migration | S |
| P4A-04 | Seed common models with real pricing data | M |
| P4A-05 | Write repository tests | M |

---

## Sub-Phase 4B: Registry-Based Routing `[PENDING]`

**Objective:** Routing service queries the model registry instead of using hardcoded model names.

**Files touched:**
- `backend/app/services/routing_service.py` (rewrite)
- `backend/tests/test_routing_service.py` (NEW)

**Dependencies:** Sub-Phase 4A, Sub-Phase 3B (policy-constrained model list)

**Implementation notes:**
- `select_route()` receives policy decision's `allowed_models` list.
- Queries registry for enabled models matching allowed list.
- Auto-routing: filter by capability → sort by cost → pick cheapest.
- Explicit routing: verify requested model exists and is enabled.

**Acceptance criteria:**
- [ ] Routing queries model registry
- [ ] Auto-routing picks cheapest compliant model
- [ ] Explicit routing validates against registry
- [ ] Policy-constrained model list respected

### Task Board — Sub-Phase 4B

| ID | Task | Est. |
|----|------|------|
| P4B-01 | Rewrite `select_route()` to use model registry | L |
| P4B-02 | Implement auto-routing: capability filter → cost sort | M |
| P4B-03 | Validate explicit model requests against registry | M |
| P4B-04 | Wire policy-allowed models into routing | M |
| P4B-05 | Write routing tests with mock registry | L |

---

## Sub-Phase 4C: Fallback Chains `[PENDING]`

**Objective:** Define fallback model/provider sequences for automatic failover.

**Files touched:**
- `backend/app/services/routing_service.py` (add fallback logic)
- `backend/app/domain/entities.py` (add `FallbackChain` entity)

**Dependencies:** Sub-Phase 4B, Sub-Phase 1E (retry already exists)

**Implementation notes:**
- Fallback chain: ordered list of (provider, model) pairs.
- On provider failure after retries, try next in chain.
- Chains configurable per policy profile (rules JSON: `fallback_chain`).

**Acceptance criteria:**
- [ ] Fallback chain definition exists
- [ ] On failure, next provider in chain attempted
- [ ] Chain exhaustion → clean error

### Task Board — Sub-Phase 4C

| ID | Task | Est. |
|----|------|------|
| P4C-01 | Define `FallbackChain` entity | S |
| P4C-02 | Implement chain traversal in routing service | M |
| P4C-03 | Wire into orchestrator retry/fallback logic | M |
| P4C-04 | Write fallback chain tests | M |

---

## Sub-Phase 4D: Cost-Aware Routing `[PENDING]`

**Objective:** Auto-routing considers per-model pricing from the registry.

**Files touched:**
- `backend/app/services/routing_service.py` (enhance auto-routing)
- `backend/app/services/usage_service.py` (use real pricing)

**Dependencies:** Sub-Phase 4A (model pricing in registry)

**Implementation notes:**
- Cost calculation: `(estimated_prompt_tokens * cost_per_input_token) + (estimated_completion_tokens * cost_per_output_token)`.
- Auto-routing ranks models by: `capability_match → cost → latency (future)`.
- Usage service uses model-specific pricing, not flat rate.

**Acceptance criteria:**
- [ ] Auto-routing prefers cheaper models when capability is equal
- [ ] Usage calculation uses per-model pricing
- [ ] Cost estimates within 20% of provider-reported costs

### Task Board — Sub-Phase 4D

| ID | Task | Est. |
|----|------|------|
| P4D-01 | Implement cost-aware ranking in auto-routing | M |
| P4D-02 | Update usage service to use per-model pricing from registry | M |
| P4D-03 | Write cost-aware routing tests | M |

---

# Phase 5: Observability + Audit Logs + Usage/Cost Tracking

> **Goal:** Every request is logged. Audit events are persisted. Usage is tracked accurately.

---

## Sub-Phase 5A: Audit Event Persistence `[PENDING]`

**Objective:** Implement the audit service to persist events to the database.

**Files touched:**
- `backend/app/models/audit_event.py` (NEW)
- `backend/app/repositories/audit_repository.py` (NEW)
- `backend/app/services/audit_service.py` (rewrite)
- `backend/migrations/versions/005_audit_events.py` (NEW)

**Dependencies:** Sub-Phase 0A, Sub-Phase 0B

**Implementation notes:**
- `AuditEventORM`: event_id (UUID), tenant_id, project_id, request_id, event_type (enum), actor, payload (JSON), created_at.
- Event types: `auth_success`, `auth_failure`, `policy_block`, `policy_mask`, `provider_call`, `provider_error`, `dlp_finding`.
- `record_success()`: creates `provider_call` event.
- `record_blocked_request()`: creates `policy_block` event.
- New: `record_auth_failure()`, `record_dlp_finding()`.

**Acceptance criteria:**
- [ ] Audit events persisted to DB
- [ ] Each event type has correct payload structure
- [ ] Events queryable by tenant, project, request_id, type
- [ ] Tests verify event creation for each type

### Task Board — Sub-Phase 5A

| ID | Task | Est. |
|----|------|------|
| P5A-01 | Create `AuditEventORM` model | M |
| P5A-02 | Create `AuditRepository` | M |
| P5A-03 | Rewrite `AuditService` with real persistence | L |
| P5A-04 | Generate Alembic migration | S |
| P5A-05 | Write audit service tests | M |

---

## Sub-Phase 5B: Request Logging Persistence `[PENDING]`

**Objective:** Persist every request to the request_logs table with full metadata.

**Files touched:**
- `backend/app/services/audit_service.py` (add request logging)
- `backend/app/services/orchestrator.py` (ensure all fields populated)

**Dependencies:** Sub-Phase 5A, Sub-Phase 0B (RequestLogRepository)

**Implementation notes:**
- Orchestrator times the full request (`start = time.monotonic()` → `latency_ms = (time.monotonic() - start) * 1000`).
- Log: tenant_id, project_id, request_id, provider, model, status (success/blocked/error), policy_action, latency_ms, prompt_tokens, completion_tokens, estimated_cost_usd.
- Blocked requests still logged (status=blocked, no provider/model).

**Acceptance criteria:**
- [ ] Every request creates a `request_logs` row
- [ ] Blocked requests logged with status=blocked
- [ ] Failed requests logged with status=error
- [ ] Latency accurately measured
- [ ] Cost populated from usage service

### Task Board — Sub-Phase 5B

| ID | Task | Est. |
|----|------|------|
| P5B-01 | Add timing to orchestrator | S |
| P5B-02 | Persist request log at end of orchestrator pipeline | M |
| P5B-03 | Handle blocked + error request logging | M |
| P5B-04 | Write request logging tests | M |

---

## Sub-Phase 5C: Usage Records + Real Cost Calculation `[PENDING]`

**Objective:** Track usage per request with accurate cost calculations.

**Files touched:**
- `backend/app/models/usage_record.py` (NEW)
- `backend/app/repositories/usage_repository.py` (NEW)
- `backend/app/services/usage_service.py` (rewrite)
- `backend/migrations/versions/006_usage_records.py` (NEW)

**Dependencies:** Sub-Phase 4A (model pricing), Sub-Phase 5A

**Implementation notes:**
- `UsageRecordORM`: record_id, request_id FK, tenant_id, project_id, provider, model, prompt_tokens, completion_tokens, total_tokens, cost_usd (Decimal), created_at.
- Usage service: prefer provider-reported tokens from `ProviderResponse`. Fall back to estimation.
- Cost: `prompt_tokens * model.cost_per_input_token + completion_tokens * model.cost_per_output_token`.

**Acceptance criteria:**
- [ ] Usage record created per request
- [ ] Provider-reported tokens used when available
- [ ] Cost uses per-model pricing
- [ ] Usage queryable by tenant, project, date range

### Task Board — Sub-Phase 5C

| ID | Task | Est. |
|----|------|------|
| P5C-01 | Create `UsageRecordORM` model | S |
| P5C-02 | Create `UsageRepository` with create + aggregation queries | M |
| P5C-03 | Rewrite `UsageService` with real cost calculation | M |
| P5C-04 | Generate Alembic migration | S |
| P5C-05 | Write usage calculation tests | M |

---

## Sub-Phase 5D: Structured Log Correlation `[PENDING]`

**Objective:** Every log line includes request_id, tenant_id, project_id for tracing.

**Files touched:**
- `backend/app/core/logging.py` (enhance)
- `backend/app/middleware/request_context.py` (bind structlog context)

**Dependencies:** Sub-Phase 0C (error handling in place)

**Implementation notes:**
- Use `structlog.contextvars` to bind request context at middleware level.
- All `logger.info/error/warning` calls automatically include bound context.
- Log entries: JSON with `request_id`, `tenant_id`, `project_id`, `event`, `level`, `timestamp`.

**Acceptance criteria:**
- [ ] All log entries include request_id
- [ ] Authenticated requests include tenant_id + project_id
- [ ] Log format is parseable JSON
- [ ] Correlation works across async tasks

### Task Board — Sub-Phase 5D

| ID | Task | Est. |
|----|------|------|
| P5D-01 | Set up `structlog.contextvars` in middleware | M |
| P5D-02 | Bind tenant/project context after auth | S |
| P5D-03 | Verify all log lines include context fields | M |

---

# Phase 6: Admin Console Integration

> **Goal:** Admin users can log in and manage the gateway through the UI.

---

## Sub-Phase 6A: Admin Authentication `[PENDING]`

**Objective:** Admin login endpoint with JWT. Protected admin routes.

**Files touched:**
- `backend/app/api/routes/admin_auth.py` (NEW)
- `backend/app/services/admin_auth_service.py` (NEW)
- `backend/app/middleware/admin_auth.py` (NEW)
- `backend/app/main.py` (register admin auth)
- `admin-ui/app/login/page.tsx` (NEW)
- `admin-ui/lib/auth.ts` (NEW)

**Dependencies:** Sub-Phase 2A (user model)

**Implementation notes:**
- `POST /admin/login` — email + password → bcrypt verify → JWT (HS256, 24h expiry).
- Admin middleware on all `/admin/*` routes (except `/admin/login`).
- JWT contains: user_id, tenant_id, role, exp.
- Admin UI: login page with email/password form. Store JWT in httpOnly cookie or localStorage.

**Acceptance criteria:**
- [ ] Login with valid credentials → JWT returned
- [ ] Invalid credentials → 401
- [ ] Admin routes without JWT → 401
- [ ] Admin routes with expired JWT → 401
- [ ] Admin UI login flow works end-to-end

### Task Board — Sub-Phase 6A

| ID | Task | Est. |
|----|------|------|
| P6A-01 | Create `POST /admin/login` endpoint | M |
| P6A-02 | Create admin JWT middleware | M |
| P6A-03 | Register admin auth in `main.py` | S |
| P6A-04 | Create admin UI login page | M |
| P6A-05 | Create admin UI auth utility (store/check JWT) | M |
| P6A-06 | Write admin auth tests | M |

---

## Sub-Phase 6B: Admin CRUD API Endpoints `[PENDING]`

**Objective:** Full CRUD endpoints for all managed entities.

**Files touched:**
- `backend/app/api/routes/admin.py` (major expansion)
- `backend/app/schemas/admin.py` (expand with create/update schemas)

**Dependencies:** Sub-Phase 6A (admin auth), Sub-Phase 0B (repositories)

**Implementation notes:**
- Tenant CRUD: `GET/POST/PUT/DELETE /admin/tenants`
- Project CRUD: `GET/POST/PUT/DELETE /admin/projects` (includes API key generation)
- Provider CRUD: `GET/POST/PUT/DELETE /admin/providers`
- Model CRUD: `GET/POST/PUT/DELETE /admin/models`
- Policy CRUD: `GET/POST/PUT/DELETE /admin/policies`
- Request logs: `GET /admin/requests` (paginated, filterable by date/project/provider/status)
- Audit events: `GET /admin/audit-events` (paginated, filterable)
- All queries scoped to admin's tenant_id.

**Acceptance criteria:**
- [ ] All CRUD endpoints return correct data from DB
- [ ] Pagination works (limit/offset)
- [ ] Filtering works (query parameters)
- [ ] Tenant isolation enforced (admin sees only their tenant's data)
- [ ] Create returns 201, not-found returns 404, delete returns 204

### Task Board — Sub-Phase 6B

| ID | Task | Est. |
|----|------|------|
| P6B-01 | Expand admin schemas (create/update models for each entity) | L |
| P6B-02 | Implement tenant CRUD endpoints | M |
| P6B-03 | Implement project CRUD + API key generation | L |
| P6B-04 | Implement provider CRUD with credential storage | M |
| P6B-05 | Implement model CRUD | M |
| P6B-06 | Implement policy CRUD | M |
| P6B-07 | Implement request log listing with filters | M |
| P6B-08 | Implement audit event listing with filters | M |
| P6B-09 | Write CRUD endpoint tests | L |

---

## Sub-Phase 6C: Admin UI — Data Wiring `[PENDING]`

**Objective:** All admin pages fetch and display real data from the backend.

**Files touched:**
- `admin-ui/lib/api.ts` (expand with typed API methods)
- `admin-ui/app/providers/page.tsx` (implement)
- `admin-ui/app/models/page.tsx` (implement)
- `admin-ui/app/policies/page.tsx` (implement)
- `admin-ui/app/requests/page.tsx` (implement)
- `admin-ui/app/audit/page.tsx` (implement)
- `admin-ui/app/usage/page.tsx` (implement)

**Dependencies:** Sub-Phase 6B (API endpoints)

**Implementation notes:**
- Use `fetch` or `swr` for data fetching.
- Each page: loading state, error state, data display.
- Tables with sortable columns.
- JWT attached to every API request.

**Acceptance criteria:**
- [ ] Each page loads real data from API
- [ ] Loading and error states handled
- [ ] Tables display all relevant columns
- [ ] JWT auth works for all requests

### Task Board — Sub-Phase 6C

| ID | Task | Est. |
|----|------|------|
| P6C-01 | Expand API client with typed methods for each entity | L |
| P6C-02 | Implement providers page (table + enable/disable) | M |
| P6C-03 | Implement models page (grouped by provider) | M |
| P6C-04 | Implement policies page (table) | M |
| P6C-05 | Implement requests page (paginated table with filters) | L |
| P6C-06 | Implement audit page (paginated table with filters) | M |
| P6C-07 | Implement usage page (aggregated stats) | M |

---

## Sub-Phase 6D: Admin UI — Forms + Actions `[PENDING]`

**Objective:** Create/edit/delete operations through the admin UI.

**Files touched:**
- `admin-ui/app/providers/` (add forms)
- `admin-ui/app/policies/` (add forms)
- `admin-ui/app/` (add component library)

**Dependencies:** Sub-Phase 6C

**Implementation notes:**
- Form components for: create provider, edit provider, create policy, edit policy.
- Confirmation dialogs for delete operations.
- Toast notifications for success/error.
- Consider installing shadcn/ui or similar for consistent components.

**Acceptance criteria:**
- [ ] Create operations work for providers, policies, projects
- [ ] Edit operations work with pre-filled forms
- [ ] Delete operations have confirmation
- [ ] Success/error feedback shown to user

### Task Board — Sub-Phase 6D

| ID | Task | Est. |
|----|------|------|
| P6D-01 | Set up component library (shadcn/ui or similar) | M |
| P6D-02 | Create provider management forms | L |
| P6D-03 | Create policy management forms | L |
| P6D-04 | Create project management forms + API key display | L |
| P6D-05 | Add delete confirmation dialogs | M |
| P6D-06 | Add toast notifications | S |

---

# Phase 7: Enterprise Controls

> **Goal:** RBAC, budget enforcement, rate limiting, alerting.

---

## Sub-Phase 7A: RBAC `[PENDING]`

**Objective:** Role-based access control for admin users.

**Files touched:**
- `backend/app/middleware/admin_auth.py` (add role checking)
- `backend/app/api/routes/admin.py` (protect write operations)

**Dependencies:** Sub-Phase 6A

**Acceptance criteria:**
- [ ] `admin` role: full CRUD access
- [ ] `viewer` role: read-only access
- [ ] `operator` role: read + limited write (enable/disable providers)
- [ ] Write operation by viewer → 403

### Task Board — Sub-Phase 7A

| ID | Task | Est. |
|----|------|------|
| P7A-01 | Implement role-checking decorator/dependency | M |
| P7A-02 | Apply role requirements to all admin endpoints | M |
| P7A-03 | Write RBAC tests | M |

---

## Sub-Phase 7B: Budget Caps + Quotas `[PENDING]`

**Objective:** Enforce spending limits per project/tenant.

**Files touched:**
- `backend/app/services/policy_engine.py` (add budget check)
- `backend/app/services/usage_service.py` (add `get_current_spend()`)

**Dependencies:** Sub-Phase 5C (usage tracking), Sub-Phase 3B (policy engine)

**Acceptance criteria:**
- [ ] Project budget limit checked before provider call
- [ ] Over-budget → request blocked with reason
- [ ] Budget usage queryable by admin

### Task Board — Sub-Phase 7B

| ID | Task | Est. |
|----|------|------|
| P7B-01 | Add `get_current_spend(project_id, period)` to usage service | M |
| P7B-02 | Add budget check to policy engine | M |
| P7B-03 | Write budget enforcement tests | M |

---

## Sub-Phase 7C: Rate Limiting (Redis) `[PENDING]`

**Objective:** Per-project rate limiting using Redis sliding window.

**Files touched:**
- `backend/app/services/rate_limit_service.py` (full rewrite)
- `backend/app/services/cache_service.py` (full rewrite — Redis client)
- `backend/app/core/redis.py` (NEW — Redis connection)

**Dependencies:** Sub-Phase 0D (Redis config)

**Implementation notes:**
- Redis connection via `redis.asyncio`.
- Sliding window: `ZADD` sorted set with timestamp scores. Count entries in window.
- Configurable per project: `requests_per_minute`, `tokens_per_day`.

**Acceptance criteria:**
- [ ] Redis client connected and health-checked
- [ ] Rate limit enforced per project
- [ ] Over-limit → 429 with Retry-After header
- [ ] Limits configurable

### Task Board — Sub-Phase 7C

| ID | Task | Est. |
|----|------|------|
| P7C-01 | Create Redis connection module | M |
| P7C-02 | Implement `CacheService` with Redis get/set/delete | M |
| P7C-03 | Implement sliding window rate limiter | L |
| P7C-04 | Wire rate limit check into orchestrator | M |
| P7C-05 | Write rate limiting tests | M |

---

## Sub-Phase 7D: Alerting + Webhooks `[PENDING]`

**Objective:** Send alerts on anomalies (budget threshold, error spikes, DLP triggers).

**Files touched:**
- `backend/app/models/alert_rule.py` (NEW)
- `backend/app/services/alert_service.py` (NEW)
- `backend/app/services/webhook_service.py` (NEW)

**Dependencies:** Sub-Phase 5A (audit events), Sub-Phase 7B (budget)

**Acceptance criteria:**
- [ ] Alert rules configurable per tenant
- [ ] Webhook delivery on alert trigger
- [ ] Retry failed webhook deliveries
- [ ] Alert history queryable

### Task Board — Sub-Phase 7D

| ID | Task | Est. |
|----|------|------|
| P7D-01 | Create alert rule model + migration | M |
| P7D-02 | Implement alert evaluation service | L |
| P7D-03 | Implement webhook delivery with retries | M |
| P7D-04 | Wire alert checks into audit service | M |
| P7D-05 | Write alerting tests | M |

---

# Phase 8: Scale / Compliance Extensions

> **Goal:** Production hardening for enterprise deployment.

---

## Sub-Phase 8A: Response Caching `[PENDING]`

**Objective:** Cache identical requests in Redis to reduce provider costs and latency.

**Dependencies:** Sub-Phase 7C (Redis)

**Acceptance criteria:**
- [ ] Cache key = hash(model + messages + relevant params)
- [ ] Cache hit → response from Redis, no provider call
- [ ] TTL configurable per policy profile
- [ ] Cache bypass for restricted data classifications

### Task Board — Sub-Phase 8A

| ID | Task | Est. |
|----|------|------|
| P8A-01 | Implement cache key generation | M |
| P8A-02 | Add cache check before provider dispatch in orchestrator | M |
| P8A-03 | Add cache write after successful provider response | M |
| P8A-04 | Add cache bypass rules (restricted data) | S |
| P8A-05 | Write caching tests | M |

---

## Sub-Phase 8B: SSO/SAML `[PENDING]`

**Objective:** Enterprise SSO integration for admin console.

**Dependencies:** Sub-Phase 6A (admin auth)

**Acceptance criteria:**
- [ ] SAML 2.0 IdP integration
- [ ] OIDC support
- [ ] User provisioning from SSO attributes
- [ ] Session management

### Task Board — Sub-Phase 8B

| ID | Task | Est. |
|----|------|------|
| P8B-01 | Implement SAML 2.0 service provider | XL |
| P8B-02 | Implement OIDC client | L |
| P8B-03 | Map SSO attributes to user roles | M |
| P8B-04 | Write SSO integration tests | L |

---

## Sub-Phase 8C: Immutable Audit Log `[PENDING]`

**Objective:** Append-only audit log with integrity verification.

**Dependencies:** Sub-Phase 5A (audit events)

**Acceptance criteria:**
- [ ] Audit entries append-only (no update/delete)
- [ ] Hash chain: each entry includes hash of previous entry
- [ ] Integrity verification endpoint
- [ ] Tamper detection

### Task Board — Sub-Phase 8C

| ID | Task | Est. |
|----|------|------|
| P8C-01 | Implement hash chain on audit events | L |
| P8C-02 | Add integrity verification query | M |
| P8C-03 | Block update/delete on audit table | S |
| P8C-04 | Write integrity verification tests | M |

---

## Sub-Phase 8D: Compliance Packs `[PENDING]`

**Objective:** Pre-built policy configurations for SOC2, HIPAA, GDPR.

**Dependencies:** Sub-Phase 3B (policy engine)

**Acceptance criteria:**
- [ ] SOC2 pack: audit logging enforced, no PII pass-through
- [ ] HIPAA pack: PHI detection + blocking, BAA verification
- [ ] GDPR pack: EU region constraints, data minimization, right to deletion

### Task Board — Sub-Phase 8D

| ID | Task | Est. |
|----|------|------|
| P8D-01 | Define SOC2 policy profile template | M |
| P8D-02 | Define HIPAA policy profile template + PHI patterns | L |
| P8D-03 | Define GDPR policy profile template + region enforcement | L |
| P8D-04 | Create compliance pack loader | M |
| P8D-05 | Write compliance pack tests | L |

---

# Execution Order (Exact Recommended Sequence)

This is the order the Builder should follow. Sub-phases within the same tier can be parallelized if multiple builders are available.

```
┌─────────────────────────────────────────────────────────────┐
│ TIER 1: Foundation (must complete before anything else)     │
│                                                             │
│   0A → 0B ──→ 0C ──→ 0D                                   │
│   (DB)  (repo)  (errors)  (config)                         │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TIER 2: Adapters + Auth (enables real gateway operation)    │
│                                                             │
│   1A → 1B → 1C → 1D    (provider adapters, sequential)     │
│         │                                                   │
│   2A → 2B → 2C → 2D    (auth, sequential)                  │
│         │                                                   │
│         └──→ 1E         (retry/fallback, needs 1B)          │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TIER 3: Governance + Routing (makes gateway smart)          │
│                                                             │
│   3A → 3B ──→ 3C → 3D  (policy + DLP, sequential)          │
│                                                             │
│   4A → 4B → 4C → 4D    (routing, sequential)               │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TIER 4: Observability + Streaming                           │
│                                                             │
│   5A → 5B → 5C → 5D    (audit + usage, sequential)         │
│                                                             │
│   1F                     (streaming, needs 1B+1C)           │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TIER 5: Admin Console                                       │
│                                                             │
│   6A → 6B → 6C → 6D    (admin, sequential)                 │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TIER 6: Enterprise                                          │
│                                                             │
│   7A ─┐                                                     │
│   7B ─┤ (can parallelize)                                   │
│   7C ─┤                                                     │
│   7D ─┘                                                     │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TIER 7: Scale + Compliance                                  │
│                                                             │
│   8A ─┐                                                     │
│   8B ─┤ (can parallelize)                                   │
│   8C ─┤                                                     │
│   8D ─┘                                                     │
└─────────────────────────────────────────────────────────────┘
```

---

# First Sub-Phase Task Pack: Phase 0A

> **This is what the Builder should implement NOW.**

## Pre-conditions
- Docker PostgreSQL is running (`docker-compose up -d postgres`)
- Python virtual environment active with `requirements.txt` installed

## Checklist

```
[ ] P0A-01: Create backend/migrations/env.py
    - Import Base from app.core.db
    - Import all models from app.models
    - Configure target_metadata = Base.metadata
    - Handle both online and offline migration modes
    - Use sync PostgreSQL URL from alembic.ini

[ ] P0A-02: Create backend/migrations/script.py.mako
    - Standard Alembic template for migration files

[ ] P0A-03: Create backend/app/models/__init__.py
    - Import ProjectORM, ProviderORM, RequestLogORM
    - This ensures all models are registered with Base.metadata

[ ] P0A-04: Add timestamps to all 3 ORM models
    - backend/app/models/project.py: add created_at, updated_at
    - backend/app/models/provider.py: add created_at, updated_at
    - backend/app/models/request_log.py: add created_at
    - Use: Column(DateTime(timezone=True), server_default=func.now())
    - Use: Column(DateTime(timezone=True), onupdate=func.now()) for updated_at

[ ] P0A-05: Verify alembic.ini
    - script_location = migrations
    - sqlalchemy.url matches docker-compose postgres URL
    - Should be: postgresql+psycopg2://gateway:gateway@localhost:5432/gateway

[ ] P0A-06: Create initial migration
    - File: backend/migrations/versions/001_initial_schema.py
    - Creates tables: projects, providers, request_logs
    - All columns with correct types
    - Indexes on tenant_id, project_id where defined

[ ] P0A-07: Verify migration
    - Run: alembic upgrade head (creates tables)
    - Run: alembic downgrade base (drops tables)
    - Run: alembic upgrade head again (idempotent)
```

## After Completion
Builder signals "Sub-Phase 0A complete" → Auditor verifies → proceed to Sub-Phase 0B.
