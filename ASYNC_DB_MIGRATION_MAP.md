# Async DB Migration Map (Phase 2, Session 2.2)

Chat hot-path synchronous Postgres operations that run on the async event loop
today, and their conversion plan. Verified against the working tree (code = truth).
Reference for Sessions 2.3–2.5. Scope: `/v1/chat/completions` only — admin/portal
sync `def` + threadpool handlers are explicitly out of scope.

## Entry points
- Non-stream: `public_chat.chat_completions` (`session: Session = Depends(get_db_session)`)
  → `orchestrator.handle_chat_completion(session=...)` (`orchestrator.py:208`).
- Stream: same handler → `orchestrator.resolve_streaming_context(session=...)` +
  an in-generator `log_session = SessionLocal()` (the request session closes when the
  `StreamingResponse` returns, so the generator owns a fresh session — `public_chat.py:600,1483`).

## Hot-path DB operations (every request unless noted)

| # | Call site (file:line) | Op | Service method | Notes |
|---|---|---|---|---|
| 1 | deps → `api_key_service.verify_key` | SELECT (+debounced UPDATE) | `verify_key` | Phase-1 debounced the write; SELECT still sync (cache-miss) |
| 2 | `orchestrator.py:232`, `:2064` | SELECT | `project_service.resolve_project_context` | every request |
| 3 | `orchestrator.py:242` (routing cfg inject) | SELECT | project routing cfg | every auto request |
| 4 | `orchestrator.py:303` | SELECT/aggregate | `quota_service.check_request_quota` | every request |
| 5 | `orchestrator.py:304` | SELECT/aggregate | `budget_service.check_budget` | every request |
| 6 | `orchestrator.py:314` | SELECT | `portal_credit_check.check_portal_credit` | portal keys only |
| 7 | `orchestrator.py:319` | SELECT | `profile_service.get_runtime_profile` | every request |
| 8 | `orchestrator.py:601,697,880`; `public_chat.py:457,565` | SELECT | `provider_key_service.get_api_key` | Phase-1 cached; cache-miss sync |
| 9 | `orchestrator.py:1968` | SELECT | `keyword_rule_service.get_rules_for_request` | Phase-1 cached; cache-miss sync |
| 10 | `orchestrator.py:1824` | `session.get(ProjectORM)` | inline | |
| 11 | `orchestrator.py:2117` | `session.get(TenantORM)` | inline | |
| 12 | `orchestrator.py:391/415, 543/583, 619/654, 807, 1118/1150, 1374/1406`; `public_chat.py:248/272, 401/438, 477/514` | **INSERT + commit** | `request_log_service.persist` + `session.commit()` | **blocked-path persists** (DLP/guardrail/moderation/rate-limit blocks) — sync write on loop |
| 13 | `orchestrator.py:418,1153,1409,1535`; `public_chat.py:275` | INSERT | `audit_service.record_blocked_request` / `record_success` | audit blob write |
| 14 | `orchestrator.py:1551` | INSERT/UPDATE | `portal_credit_check.deduct_portal_credit` | portal keys only |
| 15 | `public_chat.py:1483/1542` (in-generator) | INSERT + commit | `request_log_service.persist` on `log_session` | streaming success log |

Success-path request-log INSERT (non-stream) already goes through the **async log
queue** (`request_log_queue.enqueue`, Phase-1) — NOT in this list. Alert evaluation is
a **background sweep** now (Phase-1 B6) — not on the path. Rate-limit + cache are
already async-on-Redis.

## Services requiring an async variant (~10)
`api_key_service`, `project_service`, `quota_service`, `budget_service`,
`portal_credit_check`, `profile_service`, `provider_key_service`,
`request_log_service`, `audit_service`, (`keyword_rule_service` cache-miss). Each
keeps its sync method (admin/portal) and gains an `_async` variant (strangler).

## Conversion classification
- **Must convert (every request, still sync):** #2, #3, #4, #5, #7, #10, #11 + the
  blocked-path persists #12/#13 + auth SELECT #1.
- **Already mitigated (Phase 1) but sync on cache-miss:** #8 provider key, #9 keyword
  rules. Convert the cache-miss DB read for completeness.
- **Conditional (portal keys only):** #6, #14.
- **Skip:** admin/portal endpoints; `submit_feedback` (`public_chat.py:1656`, separate
  sync endpoint).

## Dependencies / risks (gating the heavy sessions 2.3–2.5)
1. **Drivers:** `asyncpg` (prod) + `aiosqlite` (tests) — ADDED in 2.1 (requirements).
   Backend image rebuilt.
2. **Test infrastructure (significant):** the suite runs on a **sync SQLite**
   `_integration_engine` with conftest patching `SessionLocal`. Once the chat handler
   uses `get_async_db`, every chat-endpoint integration test routes through the async
   session → needs a parallel **async SQLite engine + conftest fixture** binding
   `get_async_session_factory` to it (with `Base.metadata` created on it). Without it,
   chat integration tests fail. This is net-new async test infra.
3. **No mixed sessions:** the orchestrator + streaming path must use the async session
   *consistently* — a partial conversion would mix sync/async in one flow (explicitly
   disallowed), so 2.3–2.5 must convert the whole chain in one coherent pass.
4. **Streaming lifecycle:** the in-generator `log_session` must become an async session
   created inside the generator (the request `Depends` session can't outlive the response).
5. **Eager loading:** any relationship lazy-load on these reads must become explicit
   `selectinload`/`joinedload` (async sessions can't lazy-load).

## Surface size
~10 service files + 2 large handler files (`orchestrator.py` 2.4k LOC, `public_chat.py`
1.6k LOC) + ~30 call sites + net-new async test infra. This is the largest single
change in the migration and cannot be done partially without mixing sessions.
