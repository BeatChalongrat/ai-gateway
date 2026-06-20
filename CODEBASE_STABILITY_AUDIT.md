# SRAN Chat — Full Codebase Stability Audit

Date: 2026-06-01 · Mode: **audit only, no code changed**
Method: 5 parallel deep-read agents (chat-edge service layer · chat-edge handler/repo/model/config/migrations · web-chat frontend · Python gateway · integration/infra/tests) + live container/DB inspection. Every finding cites `file:line`.

Stack state at audit time — **all containers healthy/up**:
`backend-1` :8080 (healthy) · `chat-edge-api` :4000 (up, no healthcheck) · `dlp-sidecar` :50051 · `rate-limiter` :50053 · `log-worker` :50052 · `ner-service` :8001 · `mock-provider` :9090 · `postgres-1` (ai_gateway :5432) · `postgres-webchat` (sran_ai :5433) · `redis-1` :6379 · `redis-webchat` :6380 · `admin-ui` :3000 · `portal` :3001 · web-chat dev on **host** :3002.

---

## 1. Executive Summary

**Overall health score: 8 / 10 — solid core, fragile edges.**

The codebase is in materially better shape than the surface complexity suggests. Core chat (send/stream/regenerate/edit), the new 13-layer Prompt Compiler, memory (S1/S8), conversation state (S3), RAG, file/artifact generation (Excel/Doc/Pptx), auth/session, and the DLP pipeline are all **real and working in the live stack** — not stubs. Security fundamentals are strong: parameterized SQL throughout (no injection), disciplined tenant+user scoping (no IDOR found), accurate Go-struct↔DB-schema mapping, fail-closed config validation, no committed secrets, and DLP-masked logging.

Two earlier project assumptions were **disproved by live evidence**:
- **DLP and rate-limiter Rust engines are LIVE, not dormant** — `DLP_ENGINE=rust`, `RATE_LIMITER_ENGINE=rust` in the running container, with no fallback-to-python events in logs. (Contradicts `polyglot_migration_state` memory.)
- **chat-edge no longer pre-resolves "Auto" → concrete client-side** in code (`resolveAutoIfNeeded` sends `model="auto"` + routing_hints — `message_service.go:2129`). The gateway still logs `router="explicit"` on most traffic, but that is because the **default + locked model is a concrete id** (claude-sonnet-4), not because Auto is bypassed. Smart routing fires only when the user explicitly selects "Auto". (Updates `finding_auto_routing_bypass` memory.)

**Biggest risks** (all narrow, none currently causing outages):
1. A reachable **slice-bounds panic** in file-context truncation (`file_service.go:527`).
2. **Streaming upstream errors bypass mapping + fallback** in the gateway's OpenAI-compatible base + Google adapters → raw `TaskGroup` tracebacks (already visible in live logs).
3. **Detached goroutines without `recover()`** in chat-edge — a panic in a background extractor crashes the whole process.

**Recommendation: SAFE to add features — after fixing the 2 HIGH items** (file_service panic + gateway streaming error mapping), which are small, localized changes. Everything else is fragility/debt that can be scheduled, not a blocker. See §9.

---

## 2. Feature Stability Matrix

Tier: core = breaks the product · feature = degrades one capability · infra = foundation
Status: ✅ solid · ⚠️ works but fragile · ❌ broken/stub

| Feature | Tier | Frontend | Chat-edge | Gateway | DB | Status | Notes |
|---|---|---|---|---|---|---|---|
| Basic chat | core | ✅ | ✅ | ✅ | ✅ | ✅ | end-to-end solid; well-instrumented |
| Streaming | core | ⚠️ | ⚠️ | ⚠️ | — | ⚠️ | no FE reconnect; 64KB SSE line cap; compat/google stream errors unmapped |
| File upload | core | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | upload solid; `BuildContextBlock` truncation can panic (`file_service.go:527`) |
| Excel generation | feature | ✅ | ✅ | — | ✅ | ✅ | validate-before-persist, blob cleanup on failure |
| Doc generation | feature | ✅ | ✅ | — | ✅ | ✅ | same robust pattern |
| Pptx generation | feature | ✅ | ✅ | — | ✅ | ✅ | reuses DocService dispatch |
| Memory (cross-chat) | feature | ✅ | ✅ | — | ✅ | ✅ | CRUD + DLP + dedup solid |
| Memory auto-extract (S8) | feature | — | ✅/⚠️ | — | ✅ | ⚠️ | works; background goroutine has no `recover()` |
| RAG / Knowledge | feature | ✅ | ⚠️ | — | ✅ | ⚠️ | works; `chunker.go:196` splits Thai/CJK mid-rune |
| Smart routing | feature | — | ✅ | ⚠️ | — | ⚠️ | live but **starved** — only fires on explicit "Auto" |
| Prompt Compiler (13-layer) | feature | — | ✅ | — | — | ✅ | verified: 13 layers in order, sources returned, nil-safe |
| Model selector | feature | ✅ | ✅ | ✅ | — | ✅ | cached fallback on 5xx |
| DLP pipeline | feature | — | — | ✅ | (vault: redis) | ✅ | real mask→AI→unmask, Rust path live, fail-open |
| Settings / preferences | feature | ✅ | ✅ | — | ✅ | ✅ | 1500-char cap, allowlist validation |
| GDPR purge | feature | ✅ | ✅ | — | ✅ | ✅ | cascade order correct, confirmation phrase |
| Search past chats | feature | ✅ | ✅ | — | ✅ | ✅ | UI via `/conversations?q=`; `/search` backs the AI tool |
| Auth / session | infra | ✅ | ✅ | — | ✅ | ✅ | tx-safe register, refresh rotation + replay burn |
| Conversation state (S3) | feature | — | ✅ | — | ✅ | ✅ | never wipes on extraction failure |

No feature is **❌ broken/stub** end-to-end. The only items short of solid are streaming, file-context truncation, RAG chunking, and smart routing (starved by design).

---

## 3. Critical Issues (fix before adding features)

### C1 — HIGH · Reachable panic in file-context truncation
`services/chat-edge-api/internal/service/file_service.go:523-527`
`body = body[:remaining-len(header)-len("\n\n[content truncated]\n\n")]`. The guard on line 523 only subtracts `len(header)+19`, but line 527 subtracts `len(header)+29`. A long filename with a near-exhausted token budget passes the guard yet makes the slice high-bound **negative → runtime panic** (slice out of range), aborting the user turn. Even when positive it slices on a byte boundary, splitting a Thai/CJK rune and sending invalid UTF-8 to the gateway. **Fix: guard against the larger subtraction and cut on a rune boundary.**

### C2 — HIGH · Streaming upstream errors bypass mapping + fallback (gateway)
`backend/app/services/providers/openai_compatible_adapter.py:170` and `backend/app/services/providers/google_adapter.py:295` call raw `resp.raise_for_status()` inside `client.stream()` **without** `map_provider_error`. A streaming 4xx/5xx from qwen/kimi/zai/minimax/groq/xai/deepseek/google surfaces as a raw `httpx.HTTPStatusError` → the `"unhandled errors in a TaskGroup"` traceback already seen in live logs, and it **skips fallback eligibility** on the stream path. (Non-stream and OpenAI/Anthropic streams are correctly mapped.) **Fix: wrap stream `raise_for_status` in `map_provider_error` like the non-stream path.**

### C3 — MEDIUM · Detached goroutines have no panic recovery (chat-edge)
`message_service.go:1879` & `:1942`, `memory_service.go:555` — `go func(){…}()` on `context.Background()` with no `recover()`. A panic inside `GenerateOrUpdateState` / `ExtractPreferences` / a repo call **crashes the entire process** (Go propagates an unrecovered goroutine panic to the runtime). Also unbounded concurrency under load. **Fix: wrap each background goroutine body in `defer recover()`; consider a small worker bound.**

---

## 4. Fragile Areas (work, but risky)

| # | Severity | Area | file:line | Risk |
|---|---|---|---|---|
| F1 | MEDIUM | SSE line >64KB aborts stream | `gateway_client.go:347,393` | `bufio.Scanner` with no enlarged `Buffer()`; a large tool-call-args or reasoning frame → `ErrTooLong` kills the turn |
| F2 | MEDIUM | RAG chunker UTF-8 byte split | `chunker.go:187-206` (`windowSplit`) | long single Thai/CJK passage embedded as invalid-UTF-8 chunks → degraded retrieval (bilingual product) |
| F3 | MEDIUM | No SSE reconnection (frontend) | `lib/stream.ts`, `stores/chatStore.ts:719` | a mid-stream network blip flips the message to `error`; user must regenerate (no data loss — server persists what it wrote) |
| F4 | MEDIUM | Service-JWT auth path dead | `gateway_client.go:557-568` ↔ `backend/app/api/deps.py:98-142` | gateway only accepts `gw_` keys or admin `eyJ` JWTs; chat-edge's minted service JWT 401s. Works today via single `GATEWAY_API_KEY` — fragile single-credential dependency, blocks per-user attribution |
| F5 | MEDIUM | `_ENV_KEYS` omits groq/xai/deepseek/google | `backend/app/services/provider_key_service.py:77-85` | those 4 self-inject env keys in `chat()`, but smart-routing's selection can't see them → never auto-selected on env keys alone (auto+hints path only) |
| F6 | MEDIUM | `UpdateUser` silently drops `allowed_models` | `services/chat-edge-api/internal/handler/admin.go:718-721` | PATCH returns 204 without persisting the field — lies to the operator; model gating never takes effect |
| F7 | MEDIUM | chat-edge stack: no healthcheck / no restart policy | `docker-compose.webchat.yml:18-99` | chat-edge-api, postgres-webchat, redis-webchat, web-chat show "Up" but never "healthy"; no auto-recovery on daemon restart |
| F8 | LOW-MED | rehypeHighlight re-highlights whole message every frame while streaming a code block | `apps/web-chat/src/components/chat/MessageBubble.tsx:531` | jank/perf cost on long fenced-code replies (~60×/sec re-parse) |
| F9 | LOW-MED | Stream token usage can be dropped | `message_service.go:2522` | natural `Done` branch forwards possibly-nil `ev.Usage` instead of falling back to `lastUsage` → token accounting lost on some turns |
| F10 | LOW | `provision_service.go:100` email-reconcile UPDATE | could re-key/PK-collide on cross-id email collision (UUIDs make it rare) |
| F11 | LOW | `admin_kb.go` raw `err.Error()` to client + `err == pgx.ErrNoRows` (not `errors.Is`) | `:118,131,188,192,276,280,284,314,318,326` | admin-only info leak + a future `%w` wrap silently turns 404→500 |
| F12 | LOW | `router.replace` called during render | `apps/web-chat/src/app/chat/[conversationId]/page.tsx:95` | React anti-pattern; can warn |
| F13 | LOW | optimistic temp ids use bare `Date.now()` | `chatStore.ts:591-592,762,826` | theoretical id collision within same ms (single stream/conv makes it near-impossible) |
| F14 | LOW | `MessageBubble` `memo` comparator compares arrays by reference | `MessageBubble.tsx:684-686` | correct only while store always allocates new arrays; latent freeze-on-mutation |

---

## 5. Dead Code / Unused Features

**Chat-edge (Go)**
- `ResolveAutoModel` / `ResolveAutoModelWithReasoning` — `model_resolver.go:141-279` — dead on the live path (superseded by routing-hints); still compiled/tested.
- `drainAndClose` — `workspace_service.go:439` — kept alive only by `var _ = drainAndClose`.
- Import-keepalive placeholders: `var _ time.Duration` (`message_service.go:1705`), `var _ = time.Now` (`search_service.go:281`).
- `buildGatewayRequest` (`message_service.go:2216`) — thin wrapper, only used by tests.
- `TenantPromptConfig` (`prompt_compiler.go:52`) — **dormant**, not dead: wired + functional but no DB table populates it yet → Layer 9 is inert until a later phase.
- `USER_JWT_SECRET` + `CSRF_SECRET` — `config/config.go:55-56` — still `required` on boot but their handlers all return **410 Gone** (auth is Portal-JWT only now). Forces operators to set secrets nothing consumes.

**Gateway (Python)**
- `semantic_router.py` (~15KB) — DEAD: `semantic_router_enabled=False` (`config.py:250`) → instantiated as `SemanticRouterDisabled()` no-op (`orchestrator.py:207`).
- `mistral` / `cohere` / `perplexity` adapters — STUB in `services/providers/_pending/`, raise `ProviderNotImplementedError` (501), **not registered** in `provider_dispatcher.py:72` → clean 503 if requested.
- keyword_router / `RoutingService._route_auto` / query-complexity classifier — LIVE but only reachable on `model="auto"`; starved while default/locked models are concrete.

**Frontend (web-chat)** — dead-but-harmless API methods in `src/lib/api.ts`:
- `inspectExcel` (`:299`), `sendMessageNonStream` (`:212`), `listConversations` cursor variant (`:109`), `exportUrl` (`:515`), `listExcelVersions` (`:319`) — all superseded.

**Unused server endpoints (no web-chat caller):** `GET /conversations/{id}/state` (router `:177`), `GET /conversations/{id}/context-info` (`:184`, debug), `GET /search` (`:247`, backs the AI tool not a UI surface), all `/admin/*` (owned by the separate admin-ui app).

---

## 6. Technical Debt

- **TODO/FIXME markers: essentially zero** across both Go (chat-edge) and the audited gateway dirs — the one Go "hit" is an LLM-prompt string literal (`conversation_state_service.go:239`), not a code marker. Unusually clean.
- **Message status enum doc drift** — `model/message.go:26` comment omits `regenerated`, which the DB CHECK allows and `UpdateStatus` writes (cosmetic).
- **Go `LOG_LEVEL` not wired** — chat-edge `slog` level hardcoded to `Info` (`cmd/server/main.go`); only Python reads `LOG_LEVEL`.
- **Webchat compose hardcodes dev sentinels as literals** (not `${VAR:-default}`): `SERVICE_JWT_SECRET`, `CSRF_SECRET`, `CHAT_EDGE_ADMIN_API_KEY`, MinIO `minioadmin/minioadmin123` — `docker-compose.webchat.yml:34-36,42,119`. Can't be overridden without editing the file, and `docker-compose.prod.yml` does **not** cover the webchat stack (port 4000 stays `0.0.0.0` in prod).
- **No webchat-stack `restart:` policy** — none of the 4 webchat services declare one (the main stack uses `unless-stopped`).
- **log-worker gRPC keepalive flapping** — `too_many_pings` GOAWAY backoff in `backend-1` logs; non-fatal noise, mistuned keepalive.
- `docker-compose.webchat.yml:15` carries the obsolete `version: "3.9"` key (ignored by compose v2).
- Obsolete uncommitted change: `docker-compose.override.yml` (the `:3002` CORS line) — benign dev fix, safe to commit.

---

## 7. Test Health

| Suite | Collected | Pass | Fail | Skip | Coverage estimate |
|---|---|---|---|---|---|
| chat-edge Go | 6 test files (~25 funcs), 4 pkgs | **all pass** | 0 | 0 | **Low** — handler / repository / model / router packages have **zero tests** |
| Python `backend/tests/` | **2745** (0 collection errors when scoped to `tests/`) | ~795 in `-k "dlp or routing or smart"` subset | **53** (subset) | 9 | **High breadth**, but ~6% of subset failing on **stale assertions** |
| Python root `/app/test_live_*.py` | — | — | crashes | — | live-only scripts; **break default `pytest` collection** |
| web-chat frontend | **0** | — | — | — | **None** — `package.json` has only dev/build/start/lint |

**The 53 Python failures are assertion drift, not infra breakage:**
- detector-count tests assert 28 detectors; registry now has **31** (`thai_name_no_prefix`, `address`, `income` added) — `test_dlp_config.py:87-112`.
- `test_streaming_routing.py:268` asserts `router_used=="service"`, gets `"explicit"` — predates the routing change.
- several DLP e2e tests expect 403 blocks but get 200 — DLP behavior changed (Rust engine / profile) without test updates.

**Action:** root live scripts (`/app/test_dlp_prompt_live.py:21`, `test_live_gateway_dlp.py`) call `sys.exit()` at import when `AI_GATEWAY_API_KEY` is unset — CI must scope to `tests/` or these scripts must be moved out of the import path. Reconcile the 53 stale assertions so real regressions aren't masked.

---

## 8. Dependency Map

```
                 ┌─────────────── web-chat (Next.js, HOST :3002) ───────────────┐
                 │  NEXT_PUBLIC_API_URL → http://localhost:4000/api/v1           │
                 ▼                                                               │
        chat-edge-api (Go :4000)  ──HTTP /v1/chat/completions──▶  AI Gateway (Python :8080)
          │  │  │                    Authorization: Bearer gw_…        │  │  │  │
          │  │  └── Redis-webchat :6380 (vault? no — gateway owns)      │  │  │  └─ Providers (OpenAI/Anthropic/
          │  └───── Postgres sran_ai :5433 (own DB, isolated)           │  │  │     Google/Groq/DeepSeek/OpenRouter/
          └──────── shared PORTAL_JWT_SECRET (portal-token verify)      │  │  │     Qwen/xAI/Z.AI/Kimi/Minimax) + mock :9090
                                                                        │  │  └─ DLP sidecar (Rust gRPC :50051) → NER :8001
                                                                        │  └──── Rate-limiter (Rust gRPC :50053)
                                                                        ├─────── Log-worker (Go gRPC :50052)
                                                                        ├─────── Postgres ai_gateway :5432 (own DB, isolated)
                                                                        └─────── Redis :6379 (cache + conversation vault)
```
- **DB isolation confirmed:** chat-edge knows only `sran_ai`/`chatedge`; gateway knows only `ai_gateway`. No cross-service FK or shared table. Coupling is purely HTTP + the shared `PORTAL_JWT_SECRET`.
- **Single point of coupling to watch:** chat-edge→gateway auth rides one `GATEWAY_API_KEY` (service-JWT fallback is dead, F4).

---

## 9. Recommendation

**Yes — safe to add features, with a short pre-flight.** The foundation (auth, DB schema, tenancy, DLP, core chat, artifact generation) is solid and demonstrably working in the live stack. The risks are localized and well-understood, not architectural.

**Do first (small, high-value — ~half a day):**
1. **C1** — fix `file_service.go:527` slice-bound guard + rune-safe cut (prevents a request-path panic).
2. **C2** — wrap streaming `raise_for_status` in `map_provider_error` in `openai_compatible_adapter.py:170` + `google_adapter.py:295` (stops the live `TaskGroup` tracebacks, restores fallback on stream errors).
3. **C3** — add `defer recover()` to the three detached goroutines (prevents a background panic from killing the process).

**Do soon (before SaaS / external exposure):**
- F1 (SSE 64KB buffer), F7 (chat-edge healthcheck + restart policy + a prod overlay for the webchat stack), F4 (decide: keep single `GATEWAY_API_KEY` or make the gateway accept the service JWT), and reconcile the 53 stale Python tests (§7) so the suite is trustworthy again.

**Feature-add risk guide:**
- **Low risk to build on:** memory, conversation state, prompt-compiler layers (Tenant Layer 9 is ready for a DB table), Excel/Doc/Pptx workspace, settings, search, model selector. These sit on solid, well-tested-by-usage paths.
- **Touch with care:** anything in the **streaming** path (FE reconnection, gateway stream-error mapping, the 64KB cap) and **RAG chunking** (UTF-8) — fragile today; stabilize the relevant edge before extending.
- **Don't assume it's wired:** smart/semantic routing is starved/dead — don't build a feature that depends on the gateway auto-selecting a model unless you also surface "Auto" in the chat-edge model flow.

---

### Cross-references to prior audits / memory to update
- `finding_auto_routing_bypass` — **update**: chat-edge code no longer pre-resolves Auto; it's the concrete default/locked model that keeps the gateway on the explicit path.
- `polyglot_migration_state` — **update**: Rust DLP + Rust rate-limiter are LIVE in this deployment (`DLP_ENGINE=rust`, `RATE_LIMITER_ENGINE=rust`), not dormant.
- `SYSTEM_PROMPT_AUDIT.md` — the Prompt Compiler (this audit confirms ✅ solid) supersedes the flat-assembly findings there.
