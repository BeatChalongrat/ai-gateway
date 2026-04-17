# P1-01: Public API Inventory & Contract Gap Analysis

## Current Endpoint Inventory

| # | Method | Path | Auth | Response Model | DB-backed | Status |
|---|--------|------|------|---------------|-----------|--------|
| 1 | GET | `/health` | No | `HealthResponse` | No | Stable |
| 2 | GET | `/v1/models` | No | `ModelListResponse` | No | Hardcoded |
| 3 | POST | `/v1/chat/completions` | Bearer | `ChatCompletionsResponse` | Yes | Functional |
| 4 | GET | `/admin/providers` | **None** | `AdminListResponse[ProviderSummary]` | No | Hardcoded |
| 5 | GET | `/admin/policies` | **None** | `AdminListResponse[PolicyProfileSummary]` | No | Hardcoded |
| 6 | GET | `/admin/requests` | **None** | `AdminListResponse[RequestLogSummary]` | No | Hardcoded |
| 7 | GET | `/admin/tenants` | **None** | `AdminListResponse[TenantResponse]` | Yes | Functional |
| 8 | POST | `/admin/tenants` | **None** | `TenantResponse` | Yes | Functional |
| 9 | GET | `/admin/projects` | **None** | `AdminListResponse[ProjectResponse]` | Yes | Functional |
| 10 | POST | `/admin/projects` | **None** | `ProjectResponse` | Yes | Functional |
| 11 | GET | `/admin/api-keys` | **None** | `AdminListResponse[ApiKeyResponse]` | Yes | Functional |
| 12 | POST | `/admin/api-keys` | **None** | `ApiKeyCreateResponse` | Yes | Functional |
| 13 | DELETE | `/admin/api-keys/{key_id}` | **None** | 204 No Content | Yes | Functional |

## Gap Analysis

### GAP-1: Request Context Middleware Not Wired

**Current:** `RequestContextMiddleware` exists in `app/middleware/request_context.py` but is not registered in `app/main.py`.

**Impact:** `request_id` is always `None` in error responses. No request tracing.

**Fix (P1-03):** Wire the middleware in `main.py`.

---

### GAP-2: `/v1/models` Is Hardcoded and Stale

**Current:** Returns 3 hardcoded models: `auto`, `gpt-4o-mini`, `claude-3-5-sonnet`. Does not reflect the model registry from Phase 2.

**Impact:** Clients see stale model list. `claude-3-5-sonnet` is missing its date suffix (`-20241022`). No OpenRouter models shown.

**Fix (P1-05):** Wire to `model_registry.list_models()` + add `auto` entry.

---

### GAP-3: `ChatCompletionsRequest` Lacks Standard Generation Parameters

**Current:** `model`, `messages`, `stream`, `metadata` only. Generation params like `temperature`, `max_tokens`, `top_p` are hidden inside `metadata`.

**Impact:** Clients must guess that `metadata.temperature` is forwarded. Not OpenAI-compatible at the schema level.

**Fix (P1-02):** Add `temperature`, `max_tokens`, `top_p` as optional top-level fields.

---

### GAP-4: `ChatCompletionsResponse` Exposes Internal Fields

**Current:** `provider`, `policy_action`, `policy_reasons`, `route_reasons` are always returned.

**Analysis:** `provider` and `model` are useful for clients. `policy_action` and `policy_reasons` are gateway-specific metadata — acceptable if documented. `route_reasons` is internal debugging — should not be in the default public response.

**Fix (P1-02):** Keep `provider`, `policy_action`, `policy_reasons` as gateway extensions. Remove `route_reasons` from the default public response (or move to a `_gateway` metadata field).

---

### GAP-5: `stream: bool` Field Accepted but Not Wired

**Current:** `ChatCompletionsRequest.stream` defaults to `False`. The route ignores it — always returns non-streaming. Phase 2 added streaming at the adapter level but the route doesn't use it.

**Fix (P1-04):** Wire streaming in the route using `StreamingResponse` + adapter `stream_chat()`.

---

### GAP-6: `Message` Schema Is Minimal

**Current:** `Message` has only `role: str` and `content: str`.

**Impact:** No validation on `role` values. No support for `name` field (used by some clients for multi-turn context).

**Fix (P1-06):** Add `role` validation (allow `system`, `user`, `assistant`). `name` field is optional — add if needed, skip if not.

---

### GAP-7: No `created` Timestamp in Response

**Current:** `ChatCompletionsResponse` has no `created` field.

**Impact:** Breaks strict OpenAI compatibility. Some client libraries expect `created: int` (Unix timestamp).

**Fix (P1-02):** Add `created: int` field with current Unix timestamp.

---

### GAP-8: Error Responses Lack `request_id` (Tied to GAP-1)

**Current:** Error handler reads `request.state.request_id` but middleware isn't wired, so it's always `None`.

**Fix:** Same as GAP-1.

---

### GAP-9: No Request Validation for Empty Messages

**Current:** An empty `messages: []` list is accepted and passes through to the provider, which will return a provider-level error.

**Fix (P1-06):** Validate `len(messages) >= 1` at the schema level.

---

### GAP-10: Admin Endpoints Have No Auth

**Current:** All `/admin/*` endpoints are completely open. Anyone can create tenants, projects, API keys, or list all data.

**Note:** This is documented as deferred to Phase 9 (Enterprise Controls / RBAC). Not a P1 fix, but worth flagging.

---

## Recommended P1 Execution Plan

Based on this analysis, the P1 tasks should execute in this order:

| Order | Task | Primary Gaps Addressed |
|-------|------|----------------------|
| 1 | **P1-02** Canonical public schemas | GAP-3, GAP-4, GAP-7 |
| 2 | **P1-03** Chat endpoint stabilization | GAP-1, GAP-8 (wire middleware) |
| 3 | **P1-04** Streaming response contract | GAP-5 |
| 4 | **P1-05** Models/discovery endpoint | GAP-2 |
| 5 | **P1-06** Request validation + normalization | GAP-6, GAP-9 |
| 6 | **P1-07** Error contract enforcement | Verify all routes consistent |
| 7 | **P1-08** Route/service boundary cleanup | Thin routes, move logic out |
| 8 | **P1-09** End-to-end API integration tests | Full coverage |
| 9 | **P1-10** API docs + examples | Document everything |

**Safest first task: P1-02** — Schema changes are additive, don't break existing behavior, and unblock P1-03/04.
