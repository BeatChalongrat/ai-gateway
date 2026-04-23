# Performance Baseline Plan — AI Gateway

Status: Phase 1 (Planner). Measure & plan only — no code changes, no load runs.
Authored: 2026-04-22.
Agents: (1) Planner [this doc] → (2) Builder → (3) Auditor.

---

## 1. Executive Summary

Goal: establish a reproducible, scripted performance baseline for the gateway's four modules (ModelRouting, Safety, Governance, Observability) across its seven live LLM providers, so the team can tell whether a future change (Go rewrite, cache strategy, Presidio swap, provider pool changes) is actually an improvement. Success is a signed-off baseline report that a single script can regenerate from git SHA + env capture. Non-goal: optimisation — nothing in this phase touches code or infra; Go candidacy is FLAGGED ONLY (see §8). Baseline is run twice: with the current degraded spaCy state, and again with `en_core_web_sm` installed, so the regression pair shows both the field state and the intended state.

---

## 2. Environment Snapshot

The baseline runner captures all of these to a JSON manifest next to the run. No values filled now — every field below is `TBD (captured at run time)`.

| Item | Source | Value |
|------|--------|-------|
| OS name / version | `platform.platform()` | TBD |
| Kernel build | `uname -a` / `ver` | TBD |
| Python version | `sys.version` | TBD (expected 3.11) |
| CPU model / physical cores / logical cores | `psutil.cpu_count(logical=False/True)` | TBD |
| RAM total | `psutil.virtual_memory().total` | TBD |
| Docker version + compose | `docker --version`, `docker compose version` | TBD |
| Postgres version | `SELECT version()` | TBD |
| Redis version | `INFO server` | TBD |
| Backend concurrency | `uvicorn --workers` / `limit-concurrency` | **4 workers, limit=100** (from `backend/app/main.py:300-308`) |
| DB pool | `pool_size / max_overflow / pool_recycle` | **10 / 20 / 300s** (from `backend/app/core/db.py:5-13`) |
| Redis client timeouts | socket_timeout / connect | **1s / 1s** (`rate_limit_service.py:37`, `cache_service.py:32`) |
| Default thread executor | `asyncio` default | **16** (set at `main.py:48`) |
| spaCy models on PYTHONPATH | `spacy.util.get_package_path` | TBD — expected *none* in baseline A, `en_core_web_sm` in baseline B |
| Presidio installed? | `importlib.import_module('presidio_analyzer')` | TBD |
| sentence-transformers installed? | pip freeze | TBD (`semantic_router_enabled=False` by default, `config.py:79`) |
| Git SHA | `git rev-parse HEAD` | TBD |

---

## 3. Endpoint Inventory

Tags: `[io-bound]` = waits on DB/Redis/network; `[cpu-bound]` = regex / JSON parse / NER / hashing on the event loop; `[mixed]` = both in same handler; `[admin-only]` = excluded from load tests. Hot path = chat-completions critical path.

### 3.1 Gateway hot-path (load-tested)

| Route | Method | Module | Tag | File:Line | Hot path? | Notes |
|-------|--------|--------|-----|-----------|-----------|-------|
| `/v1/chat/completions` | POST | All 4 | mixed | `backend/app/api/routes/public_chat.py:52` | YES | Non-stream → orchestrator. Stream → inline pipeline (same steps). Skipped by TimeoutMiddleware (`middleware/timeout.py:27`). |
| `/v1/feedback` | POST | Observability | io | `public_chat.py:450` | no | Post-hoc thumbs-up/down. |
| `/v1/models` | GET | ModelRouting | cpu-light | `public_models.py:10` | secondary | Reads in-memory `model_registry`. No DB. |
| `/health` | GET | — | io | `routes/health.py:20` | secondary | 10s cached; otherwise DB `SELECT 1` + Redis ping. |

Note: **no standalone `/v1/dlp/scan` or `/v1/guardrails/check` endpoints exist in the public API**. The user-asked endpoints map to:
- DLP scan path → exercised only via `POST /v1/chat/completions` (inbound + outbound inside orchestrator) and admin-only `POST /admin/dlp/test` (`admin_dlp.py:434`).
- Guardrails check → exercised only via `/v1/chat/completions` (orchestrator step 4b). Moderation likewise (step 4b2).
- Router decisions → `_apply_router_chain` inside the orchestrator; observable downstream via `GET /admin/routing/stats` (`admin.py:1289`), not a per-request endpoint.

This is a finding worth calling out to Builder: the baseline can't load-test DLP/guardrails in isolation via a public URL — it must either (a) hit the chat endpoint with crafted payloads that force each sub-pass, or (b) the Builder adds internal-only test endpoints (NOT recommended — pollutes prod surface). See §7 stance.

### 3.2 Public auth / portal (load-tested at lower RPS)

| Route | Method | Module | Tag | File:Line | Hot path? | Notes |
|-------|--------|--------|-----|-----------|-----------|-------|
| `/auth/login` | POST | — | cpu (bcrypt) + io | `auth.py:109` | no | Bcrypt hash in sync path. |
| `/auth/refresh` | POST | — | io | `auth.py:145` | no | |
| `/auth/me` | GET | — | io | `auth.py:171` | no | |
| `/portal/auth/signup` | POST | Portal | io + bcrypt | `portal_auth.py:41` | no | |
| `/portal/auth/login` | POST | Portal | cpu + io | `portal_auth.py:54` | no | |
| `/portal/auth/refresh` | POST | Portal | io | `portal_auth.py:62` | no | |
| `/portal/auth/logout` | POST | Portal | io | `portal_auth.py:70` | no | |
| `/portal/auth/oauth/callback` | POST | Portal | io | `portal_auth.py:85` | no | External IdP call. |
| `/portal/auth/public-settings` | GET | Portal | io | `portal_auth.py:109` | no | |
| `/portal/me` | GET | Portal | io | `portal_me.py:51` | no | Single row read. |
| `/portal/me` | PATCH | Portal | io | `portal_me.py:77` | no | |
| `/portal/me/avatar` | POST | Portal | mixed (Pillow) | `portal_me.py:103` | no | Image decode on event loop — check. 5 MB body cap. |
| `/portal/me/avatar` | DELETE | Portal | io | `portal_me.py:155` | no | |
| `/portal/me/usage` | GET | Observability | io (aggregation) | `portal_me.py:172` | no | |
| `/portal/me/transactions` | GET | Portal | io | `portal_me.py:232` | no | |
| `/portal/billing/packs` | GET | Portal | io | `portal_billing.py:29` | no | |
| `/portal/billing/purchase` | POST | Portal | io | `portal_billing.py:53` | no | Returns 501 stub. |
| `/portal/projects` | GET | Portal | io | `portal_projects.py:76` | no | |
| `/portal/projects` | POST | Portal | io | `portal_projects.py:93` | no | |
| `/portal/projects/{id}` | GET | Portal | io | `portal_projects.py:111` | no | |
| `/portal/projects/{id}` | PATCH | Portal | io | `portal_projects.py:120` | no | |
| `/portal/projects/{id}` | DELETE | Portal | io | `portal_projects.py:136` | no | |
| `/portal/projects/{id}/keys` | GET | Portal | io | `portal_projects.py:159` | no | |
| `/portal/projects/{id}/keys` | POST | Portal | io | `portal_projects.py:192` | no | |
| `/portal/projects/{id}/keys/{key_id}` | DELETE | Portal | io | `portal_projects.py:216` | no | |

### 3.3 Admin (excluded from load tests — `[admin-only]`)

These are run a single time in "smoke" so we catch obvious regressions but are not part of steady / ramp / soak.

| Route | Method | File:Line | Notes |
|-------|--------|-----------|-------|
| `/admin/dashboard` | GET | `admin.py:82` | Sync handler, 3 COUNT queries + registry scan. |
| `/admin/dashboard/overview` | GET | `admin_dashboard.py:179` | Period KPIs + deltas. |
| `/admin/dashboard/volume` | GET | `admin_dashboard.py:251` | Time-bucket rollup. |
| `/admin/dashboard/top-models` | GET | `admin_dashboard.py:309` | |
| `/admin/dashboard/recent-requests` | GET | `admin_dashboard.py:345` | |
| `/admin/providers` | GET | `admin.py:141` | |
| `/admin/providers/{name}/key` | PUT | `admin.py:167` | Writes encrypted key. |
| `/admin/providers/{name}/key/status` | GET | `admin.py:186` | |
| `/admin/providers/{name}/keys` | GET | `admin.py:198` | |
| `/admin/providers/{name}/keys` | POST | `admin.py:210` | Real external validation call — slow. |
| `/admin/providers/{name}/keys/{key_id}` | DELETE | `admin.py:253` | |
| `/admin/providers/{name}/keys/{key_id}/test` | POST | `admin.py:266` | External call. |
| `/admin/providers/{name}/health` | GET | `admin.py:288` | External call, 10s timeout. |
| `/admin/models` | GET | `admin.py:325` | |
| `/admin/policies` | GET / POST / PUT / DELETE | `admin.py:344-447` | |
| `/admin/requests/{id}` | GET | `admin.py:472` | Raw-PII gated by role. |
| `/admin/requests` | GET | `admin.py:545` | Pagination up to 5000. |
| `/admin/usage/summary` | GET | `admin.py:607` | |
| `/admin/tenants` + subpaths | all | `admin.py:646-730` | CRUD. |
| `/admin/projects` + subpaths | all | `admin.py:738-850` | CRUD. |
| `/admin/api-keys` + subpaths | all | `admin.py:858-965` | CRUD. |
| `/admin/alerts`, `/admin/alerts/{id}`, `/admin/alerts/{id}/events`, `/admin/alert-events` | all | `admin.py:1004-1152` | |
| `/admin/keyword-rules`, `/admin/routing/keyword-rules` | all | `admin.py:1186-1281` | |
| `/admin/routing/stats` | GET | `admin.py:1289` | 24h rollup. |
| `/admin/dlp/detectors` | GET | `admin_dlp.py:35` | |
| `/admin/policy-profiles` | GET | `admin_dlp.py:81` | |
| `/admin/policy-profiles/{id}/category-actions` | PATCH | `admin_dlp.py:100` | |
| `/admin/ner/status` | GET | `admin_dlp.py:141` | |
| `/admin/settings` | PATCH | `admin_dlp.py:164` | Triggers NER load in background. |
| `/admin/dlp/coverage` | GET | `admin_dlp.py:382` | |
| `/admin/dlp/test` | POST | `admin_dlp.py:434` | Full DLP pipeline, no persist. **Useful for module isolation tests — see §5.** |
| `/admin/ai-review/*` | 9 endpoints | `admin_ai_review.py:123-640` | AI-assisted finding review. |
| `/admin/portal-users` + subpaths | all | `admin_portal_users.py:46-206` | |
| `/admin/system-settings` | GET, PATCH | `admin_system_settings.py:66-86` | |
| `/admin/token-packs` + subpaths | all | `admin_token_packs.py:57-117` | |
| `/metrics` | GET | `main.py:266` | In-memory counters (see §6). |

Endpoint counts: ~85 total routes across 14 router files. Hot path: 1 (`POST /v1/chat/completions`).

---

## 4. Metrics Matrix (hot-path endpoints only)

All numbers are "To be set after baseline". Proposed target column reflects a plausible starting guess, rationale explains how it was picked. Nothing becomes a binding SLO until Builder + user agree.

### 4.1 `POST /v1/chat/completions` — non-streaming, DLP-lite input (single email)

| Metric | Proposed target | Rationale |
|--------|-----------------|-----------|
| p50 latency | TBD after baseline | Dominated by provider. Provider itself is 300-2000ms; gateway adds ~20-100ms overhead. Baseline sets the truth. |
| p95 latency | TBD after baseline | Must be reported separately for each provider — aggregating p95 across providers is useless. |
| p99 latency | TBD after baseline | Tail driven by retries (`call_with_retry`, `provider_max_retries=2`, `config.py:61`). |
| RPS (steady) | TBD after baseline | Gateway-only: 4 workers × limit_concurrency 100 / expected provider p50 → order-of-magnitude upper bound. Real bound is provider rate-limit + Redis rate-limit. |
| Concurrency | TBD after baseline | Per-key 60 req/min ceiling from `DEFAULT_KEY_LIMIT` (`rate_limit_service.py:21`). Load test must use a pool of keys. |
| Gateway overhead (p50) | Proposed: ≤50 ms | Sum of: orchestrator.resolve + rate-limit + DLP (regex-only) + guardrail + routing. Rationale: every component is O(kB) regex or single Redis op in local dev. Validate with per-phase timings. |
| Gateway overhead (p95) | Proposed: ≤150 ms | Allows for a cold NER pass + a Redis slow path. Tighten after baseline. |

### 4.2 `POST /v1/chat/completions` — streaming

| Metric | Proposed target | Rationale |
|--------|-----------------|-----------|
| Time-to-first-byte (TTFB) p50 | TBD | Stream path buffers completion before emitting (v1 restriction, `public_chat.py:272-440`). TTFB = provider completion time + gateway overhead — not the usual streaming TTFB. Capture this mismatch explicitly. |
| Total stream duration p95 | TBD | |

### 4.3 `POST /v1/chat/completions` — DLP-heavy input (multi-email, SSN, Thai National ID, NER triggers)

| Metric | Proposed target | Rationale |
|--------|-----------------|-----------|
| Δ vs DLP-lite (p50) | Proposed: ≤+20 ms without NER; ≤+300 ms with NER | Regex-only is ~21 detectors over <2 kB — fast. NER (Presidio + spaCy `en_core_web_sm`) is the well-known cost spike. This is what spaCy A/B (§5) quantifies. |
| Δ vs DLP-lite (p95) | TBD | |

### 4.4 Cache-hit path (orchestrator step 6b)

| Metric | Proposed target | Rationale |
|--------|-----------------|-----------|
| p50 latency | Proposed: ≤30 ms | DLP + hash + Redis GET + JSON parse + DB insert for the log row. Sanity-check this is actually cheap. |
| Hit rate | Target: measure only | `CacheService.get_stats` exposes hit rate per project (`cache_service.py:80`). Baseline reports the rate; it's not a pass/fail. |

### 4.5 Per-provider breakout

For each of the 7 live providers + the "auto" gateway route: p50/p95/p99, error rate, retry rate. No targets proposed; rationale: the gateway does not control upstream provider latency, so targets for these are external-driven. Record as-is to establish a baseline.

---

## 5. Test Scenarios

Every scenario runs twice: baseline A = no spaCy model installed (current); baseline B = `en_core_web_sm` installed. Reports include both and a delta table.

### 5.1 Smoke (1 user / 1 min)
- Purpose: sanity gate — if smoke breaks, every downstream number is suspect.
- Profile: 1 VU, ~1 RPS, one sequence covering login → chat (non-stream) → chat (stream) → feedback → `/admin/dashboard`.
- Pass/fail: all 200s; p99 < 10× expected p50 (crude outlier gate).
- Compare: absolute values only (no prior baseline on first run).

### 5.2 Steady load (constant RPS at expected prod)
- Purpose: "does it hold at expected production rate without drift".
- Profile: 10 RPS on `/v1/chat/completions` for 10 min, mixed DLP-lite + DLP-heavy at 70/30. Mocked provider (see §7).
- Pass: p95 gateway overhead ≤ §4 proposed target; error rate < 0.1%; no event-loop lag warnings from `_loop_monitor` (`main.py:147`); DB pool `checked_out` never hits 30.
- Fail: any of the above OR memory RSS grows > 5% over the window.

### 5.3 Ramp-up (0 → N users over 5 min)
- Purpose: find the breaking point (the RPS where p95 doubles or error rate > 1%).
- Profile: 0 → 200 VU over 5 min, open model. Capture "saturation RPS" and the handler that first shows degradation.
- Pass/fail: record breaking point; no numeric target — finding it IS the deliverable.

### 5.4 Spike (sudden 10×)
- Purpose: validate rate limiter + timeout behaviour.
- Profile: 1 min at 5 RPS → instant jump to 50 RPS for 2 min → back to 5 RPS.
- Pass: rate limiter correctly returns 429 + Retry-After; TimeoutMiddleware fires 504 on stuck requests (`middleware/timeout.py:14`); event loop recovers (no lag > 500 ms after the spike ends).

### 5.5 Soak (1 hour sustained)
- Purpose: catch memory / FD / connection leaks.
- Profile: 5 RPS on `/v1/chat/completions` for 60 min.
- Pass: RSS delta < 5% end-vs-start; open file descriptor count flat ±10; DB pool `checked_out` flat; no unhandled exceptions in structlog output; Redis client stable.

### 5.6 Per-module isolation

| Module | Scenario | How to isolate |
|--------|----------|----------------|
| DLP (regex only) | 50 RPS on `POST /admin/dlp/test` (existing endpoint, `admin_dlp.py:434`) with 1 kB / 4 kB / 16 kB (cap) payloads. | Endpoint runs the full detector registry without persist or provider. Needs admin auth. |
| DLP (with NER) | Same as above, after enabling `ner_enabled=True` via `PATCH /admin/settings`. | Captures Presidio cost delta. |
| Guardrails | No dedicated endpoint — crafted payloads to `/v1/chat/completions` with jailbreak markers; mock provider so rejection returns 403 fast. | Not a true isolation; Builder may optionally add an internal test flag. |
| Routing | Crafted payloads with keyword triggers (see `DEFAULT_RULES` in `keyword_router.py:33`); mock provider. | Semantic router is flag-gated off by default; baseline B may flip it on to capture cost. |
| Chat-proxy | Mocked provider returning fixed body. | The only way to measure gateway-only overhead without paying real provider cost or latency. |
| Observability | Scrape `/metrics` every 10s during steady load; confirm counter monotonicity and no cardinality explosion. | |

### 5.7 spaCy A/B

Run the full 5.1-5.6 matrix twice:
- **A — degraded (current):** no `en_core_web_sm`. Expect Presidio returns `[]` silently (`presidio_ner.py:60-70` logs `dlp_presidio_no_spacy_model`).
- **B — intended:** `python -m spacy download en_core_web_sm` on the host/container. Expect non-zero NER findings + measurable latency delta.

Delta report: side-by-side table for p50/p95/p99 of each scenario + absolute ms delta + relative % delta. Flags the "cost of turning NER on" in concrete numbers — this is one of the primary reasons this baseline exists.

---

## 6. Instrumentation Requirements

### 6.1 App-level (per-phase timing)

Existing in the code already — structured log lines emitted by `structlog` at each orchestrator step. Confirmed via grep:
- `dlp_scan_complete` with `dlp_ms` (`orchestrator.py:198-204`)
- `moderation_scan_complete` with `moderation_ms` (`orchestrator.py:305-311`, streaming at `public_chat.py:179-186`)
- `provider_complete` with `provider_ms` (`orchestrator.py:461-467`)
- `cache_hit` with `total_ms` (`orchestrator.py:400-405`)
- `route_selected` (`orchestrator.py:369-374`)
- request-timing middleware logs `<- METHOD PATH STATUS Nms` for every request (`middleware/request_timing.py:47`)
- `MetricsMiddleware` increments in-memory counters served at `GET /metrics` — counts by path + by status only, no latency histogram (`middleware/metrics.py:1-54`). **This is the single biggest observability gap** — there's no p95/p99 percentile output from the app itself.

**Builder action required (NOT in this phase):**
- Add per-phase duration labels as histograms, not counters. Suggested label set (no new code written now):
  - `phase`: `resolve_ctx`, `rate_limit`, `dlp_input`, `guardrail`, `moderation`, `router_chain`, `route_select`, `cache_get`, `provider_call`, `dlp_output`, `transform_restore`, `persist_log`, `alert_evaluate`.
  - `provider`: `openai | anthropic | openrouter | minimax | qwen | kimi | zai | google | groq | deepseek | xai | mistral | cohere | perplexity`.
  - `outcome`: `success | blocked_dlp | blocked_guardrail | blocked_moderation | rate_limited | provider_error | timeout`.
- Cardinality cap: `path` label must use the FastAPI route template, not the concrete path (`/v1/feedback/{id}` not `/v1/feedback/abc-123`). Current MetricsMiddleware violates this rule — `scope["path"]` is the raw path (`middleware/metrics.py:38`). Flag for Builder.

### 6.2 System-level (per-process)

Capture out-of-band, pinned to Windows dev host in baseline A.

| Signal | Tool | Notes |
|--------|------|-------|
| CPU per core | `psutil.cpu_percent(interval=1, percpu=True)` | Sample every 1s. |
| RSS | `psutil.Process(pid).memory_info().rss` | Per worker PID. |
| Open FDs | `psutil.Process.num_fds()` on Linux; `psutil.Process.num_handles()` on Windows. | The baseline may be run first on Windows (dev host). Include platform-specific field. |
| GC pauses | `gc.get_stats()` snapshot every 10s | Delta between samples approximates pause time. |
| Thread count | `psutil.Process.num_threads()` | Cross-check the 16-worker executor doesn't balloon. |
| Event-loop lag | Already emitted by `_loop_monitor` (`main.py:147-160`) as log lines. | Need to parse; no histogram yet. |

Profilers (one-shot, off the steady path):
- `py-spy record -p <pid> -d 30` for a 30-second flame graph while a load test runs. Cheap, sampling, no code changes.
- `scalene` for the DLP/regex + routing paths to attribute CPU between Python and C extensions (`psycopg`, `orjson`, `httpx`, Presidio).

### 6.3 Dependency-level

| Dependency | Signal | How |
|------------|--------|-----|
| Postgres | active connections, lock_waits, query p95 | `pg_stat_activity` every 10s; `pg_stat_statements` if installed. |
| Postgres pool | `engine.pool.checkedout / size / overflow` | Already exposed in `/health` response (`health.py:49-57`). Builder should sample this as a gauge. |
| Redis | command latency, INFO stats | `redis-cli --latency` alongside, or `INFO commandstats` snapshot every 1 min. |
| Per-provider | p95, error rate, retry count | Already logged (`provider_complete`), plus `call_with_retry` in `error_handler.py` — but only as log events. Flag for Builder: promote to histogram. |

**Existing instrumentation summary:**
- structlog (JSON) — widespread, 18 files.
- In-memory counter middleware (`/metrics`) — path + status only.
- `_loop_monitor` — prints to stdout when loop is blocked >100 ms.
- DB pool stats inside `/health`.
- Cache hit/miss counters in Redis (`cache_service.py:93`).
- **No Prometheus, no OTEL, no statsd** — confirmed: grep `prometheus|statsd|opentelemetry|otel` in `backend/` returns zero hits.

---

## 7. Tool Choice — Load Generator

**Primary: k6.** **Backup: Locust.**

| Dimension | k6 | Locust | Vegeta |
|-----------|----|---|---|
| Scripting language | JS (goja) — team is mostly Python + TS, JS is acceptable. | Python — best team fit on paper. | HCL + CLI flags — awkward for multi-step scenarios. |
| Scenario flexibility | Very good: per-scenario executors (constant-arrival, ramping-vus, spike). | Good: event hooks + classes. | Limited: pure rate generator. |
| Report output | Native JSON summary + Prom / InfluxDB exporters. | HTML + CSV. | Binary → vegeta report (markdown / JSON / histogram). |
| Windows support | Native binary, zero-fuss. | Runs fine on Windows. | Native binary. |
| Cloud / CI later | k6 Cloud or GH Actions image ready. | Distributed runner needs extra glue. | Trivially scriptable. |
| Scenario isolation between modules | Scenarios-as-code — one file, many named scenarios. | Requires multiple shapes. | Would need multiple CLI invocations. |

Pick k6 for primary because the per-scenario executor model maps 1-to-1 onto §5 (smoke, steady, ramp, spike, soak are named k6 executors). Locust stays as backup for cases where Python-native access to the repo's schemas.public types matters (e.g. fuzz-style payload generation reusing `ChatCompletionsRequest`).

**Provider stance:**
- For **chat-proxy** and **routing** isolation tests: **mock the provider**. Stand up a local httpx-compatible endpoint (Builder's choice — FastAPI stub in `/tmp`, or intercept via env var pointing `*_BASE_URL` at localhost). Rationale: real provider calls (a) cost money, (b) add 500-2000 ms of variance that swamps any gateway signal, (c) exhibit provider-side rate limits that break soak.
- For **validation smoke** (§5.1 only): hit one real provider (OpenAI) once to confirm end-to-end works — cost cap: no more than 50 real calls across the whole baseline run.
- For **DLP/guardrails/observability isolation**: mock is fine (they don't call providers).

Explicit: never run ramp / spike / soak against real providers — flag in the run script as a hard guard.

---

## 8. Go Migration Candidates (flag-only)

Four components are worth a "is Go a good fit" post-baseline look. Each has a concrete, numeric decision threshold so Builder/Auditor have a crisp go/no-go.

### 8.1 DLP regex engine

- **Current:** `backend/app/services/dlp/detectors/registry.py` + 21 per-detector modules under `backend/app/services/dlp/detectors/`. All Python `re` compiled at import, iterated linearly in `scan_text` (`registry.py:86-92`).
- **Why Go might help:** (a) 21 regexes per request all run on the event-loop thread (no `await`, no `to_thread`). (b) Go's `regexp` is RE2-based — linear-time worst-case, no catastrophic backtracking, and native goroutines let each detector run in parallel without the Python GIL. (c) Go-based RE2 typically benches 2-5× faster than Python `re` on scanning workloads, before parallelism.
- **Interface:** function call (`registry.scan_text(text) -> list[DetectionMatch]`). Migration surface area: **M** — would need an IPC/grpc/HTTP hop OR a shared-memory CFFI library. HTTP microservice is the lowest-risk path; introduces one extra hop (~0.2-1 ms localhost).
- **Decision threshold:** migrate if baseline shows DLP regex (input + output combined) accounts for **≥25% of gateway overhead p50** OR **≥100 ms p95** on DLP-heavy payloads. If DLP-heavy adds only a few ms over DLP-lite, regex is not the bottleneck — don't move it.
- **Likely-NOT-worth-migrating:** if NER dominates (baseline B result), the Go rewrite of regex doesn't help Presidio — Presidio itself stays in Python / spaCy. Only migrate regex when regex IS the cost.

### 8.2 Semantic routing embedding cache

- **Current:** `backend/app/services/semantic_router.py`. Encoder lazy-loaded from `sentence-transformers` + torch (`semantic_router.py:150-161`). Anchors computed once at startup. Cosine math is a hand-rolled pure-Python loop (`semantic_router.py:206-218`). Flag-gated off by default (`config.py:79`).
- **Why Go might help:** the cosine loop is tight-math per-request; Go with SIMD (gonum) is 10-50× faster than a Python `for-loop`. That said — the cost dwarf here is sentence-transformers + torch inference, NOT cosine math. Rewriting the cosine loop in Go saves µs; nothing.
- **Interface:** method call on orchestrator-owned object.
- **Decision threshold:** migrate **only if** the baseline (which requires semantic_router_enabled=True, so consider a third baseline run, "C = semantic router on") shows cosine math accounts for >10 ms p95. Almost certainly won't — real cost is embedding inference. Flagged here so Builder doesn't spend time on the wrong thing.
- **Likely-NOT-worth-migrating:** Yes — this is the clearest "don't bother" entry. Embedding inference is the bottleneck; port that to a dedicated inference server or ONNX instead of rewriting the router in Go.

### 8.3 Rate limiter

- **Current:** `backend/app/services/rate_limit_service.py`. Redis `ZADD + ZREMRANGEBYSCORE + ZCARD + EXPIRE` pipeline per scope, 3 scopes per request → 12 Redis calls per chat completion. All synchronous via `redis-py` (not `redis.asyncio`).
- **Why Go might help:** rate limit is called on every request; it blocks the event loop (sync redis call, `rate_limit_service.py:34-39`). Go's goroutine-per-request model + lua-scripted atomic check would move the 12-op pipeline to a single EVAL and eliminate the event-loop block.
- **Interface:** function call from orchestrator. Low-surface refactor: swap the class for an async one OR call out to a tiny Go HTTP service.
- **Decision threshold:** migrate if baseline shows:
  - rate-limit phase latency ≥5 ms p95 (currently a sync redis round-trip, typical 0.5-2 ms — so this may not be it), OR
  - event-loop lag warnings correlate with rate-limit calls, OR
  - rate-limit throughput at steady RPS is within 20% of max Redis pipeline capacity (would force a Go + Redis Lua script to get more headroom).
- **Cheaper pre-Go fix to try first (flag only; Builder decides):** use `redis.asyncio` + a single Lua EVAL for all 3 scopes in one round-trip. If that closes the gap, don't rewrite in Go.

### 8.4 Proxy / provider layer

- **Current:** `backend/app/services/provider_dispatcher.py` + 14 adapters in `backend/app/services/providers/`. Each call creates a **new** `httpx.AsyncClient` (no connection pooling across requests — see `openai_adapter.py:56`, `http_client.py:46`). Streaming uses the same per-call client pattern.
- **Why Go might help:** concurrent outbound HTTP connection handling is exactly where Go shines (goroutines + net/http keep-alive pool). The 14 adapters could collapse into a tiny Go shim with provider-specific request/response translation.
- **Interface:** async function returning `ProviderResponse` / `AsyncIterator[ProviderStreamChunk]`. Refactor surface: **L** — 14 adapters, 7 live providers, different auth flows, streaming semantics, error retries.
- **Decision threshold:** **likely NOT worth migrating.** Bottleneck is upstream provider network, not Python's httpx client. Migrate ONLY if baseline shows:
  - gateway-side provider-dispatch latency ≥50 ms p95 (excluding provider time), OR
  - event-loop lag during streaming runs traces back to the adapter code (unlikely — httpx is C-accelerated), OR
  - connection churn shows up in `netstat` as TIME_WAIT buildup at sustained RPS.
- **Cheaper pre-Go fix to try first:** share a single httpx.AsyncClient per provider across the app's lifetime (currently created per-call). This one change likely closes 80% of the gap; flagged because it's a code change, not a Go migration.

### 8.5 Flag-only summary

| Component | Recommend Go? | Confidence | Cheaper non-Go fix to try first |
|-----------|---------------|------------|----------------------------------|
| DLP regex | Conditional on baseline | Medium-high IF regex is the bottleneck | None — Python `re` is already near-optimal for Python. |
| Semantic router cosine | **No** | High | Don't bother; cost is embedding, not cosine. |
| Rate limiter | Conditional | Medium | Try `redis.asyncio` + single Lua script first. |
| Proxy / adapter layer | **No** (probably) | High | Share httpx clients across calls. |

---

## 9. Reproducibility Checklist

Each baseline run MUST produce a `run_<timestamp>/` directory containing:

- `manifest.json`:
  - git SHA (`git rev-parse HEAD`) + `git diff --stat HEAD` (clean or dirty flag).
  - Branch name.
  - Host: OS, kernel, CPU, logical cores, total RAM.
  - Python version, Python implementation (CPython expected).
  - Docker engine + compose versions.
  - Docker image digests for postgres + redis (`docker inspect --format='{{.Image}}'`).
  - Environment variables present, with secrets redacted (`JWT_SECRET=***`, `*_API_KEY=***` but key presence/absence preserved).
  - spaCy models installed (`spacy validate` or `spacy info`).
  - Presidio version (`presidio-analyzer`).
  - sentence-transformers version (if present).
  - Baseline variant: A (degraded) or B (with `en_core_web_sm`) or C (semantic router on).
- `scenario.json`: the k6 scenario config used, verbatim, with resolved VU / RPS profile.
- `timestamps.json`: run start, run end, per-scenario start/end (monotonic + wallclock).
- `raw/*.json`: k6 raw result JSON per scenario.
- `raw/metrics_snapshot_start.json`, `metrics_snapshot_end.json`: `/metrics` contents + `/health` DB-pool numbers at scenario bounds.
- `raw/pyspy_flamegraph.svg` (optional, only if the profiling step is run).
- `raw/psutil_samples.csv`: per-second system samples (CPU per core, RSS, FDs, threads, GC gen0/1/2).
- `raw/postgres_stat.json`: `pg_stat_activity` + `pg_stat_statements` (if installed) pre/post.
- `raw/redis_info.json`: `INFO` command output pre/post.
- `report.md`: generated processed summary — one markdown file a reviewer can read.
- `diff_vs_previous.md`: if a previous baseline directory is provided, per-metric delta table.

Requirement: everything is machine-readable so `compare_runs.py` (Builder phase) can diff two runs deterministically.

---

## 10. Open Questions / Risks

Items the Planner couldn't resolve from the repo alone. Builder cannot start until these are answered.

1. **Expected production RPS on `/v1/chat/completions`.** Is this team-level (single-digit RPS), pilot (10-50 RPS), or at-scale (100+ RPS)? This drives the §5.2 steady profile. Repo has no hint.
2. **Peak concurrency target per API key.** `DEFAULT_KEY_LIMIT=60 req/min` is the current per-key ceiling (`rate_limit_service.py:21`). Is 60/min the desired production number, or is that a dev placeholder?
3. **SLOs already agreed with stakeholders.** Do p95/p99 latency numbers exist anywhere (runbook, contract, internal doc)? If yes, they replace the "TBD after baseline" cells in §4. RUNBOOK.md is present in repo — Planner did not exhaustively grep it; flagging for user confirmation.
4. **Mock provider stance: OK or not?** §7 recommends mocked providers for all scenarios except a 50-call smoke. Needs user sign-off — some teams insist on real-provider baselines for realism.
5. **Windows vs Linux baseline precedence.** Runbook says "Windows dev host, likely Linux prod". Should baseline A run on Windows (dev box) and baseline B on Linux (prod-like container)? Or Windows for both variants? Affects `psutil.num_fds` vs `num_handles` in §6.
6. **NER model choice.** Baseline B assumes `en_core_web_sm`. Production-facing readme mentions `en_core_web_lg` fallback (`presidio_ner.py:38-44`). Which is the target? Affects both install cost and detection quality.
7. **Data retention during soak.** Soak writes ~18k request_log rows per hour at 5 RPS. Must the baseline truncate the table before / after, or does the existing retention job (`_daily_dlp_retention` at `main.py:163`) handle it? Clean-up policy needed.
8. **Streaming TTFB semantics.** `public_chat.py:272-440` buffers the full stream before emitting, so "TTFB" is effectively "total time". User must confirm whether baseline reports this faithfully (as total time) OR whether Builder is expected to fix the buffer-first design first and THEN re-baseline. These are two very different plans.
9. **Include `semantic_router_enabled=True` as a third baseline variant (C)?** Currently flag-gated off. If the team wants to measure cost-of-semantic-routing, this is a third matrix run with its own report. Needs approval — it requires installing sentence-transformers + torch, which is hundreds of MB.
10. **Budget for baseline runs.** Real-provider smoke is capped at 50 calls. Any higher cap? Any preferred provider for the smoke?

---

## Appendix A — Key file paths cited

- `backend/app/main.py:206-262` — app + router wiring + middleware order.
- `backend/app/main.py:40-80` — lifespan: executor, NER preload, loop monitor, retention.
- `backend/app/services/orchestrator.py:109-650` — full request pipeline.
- `backend/app/services/dlp/detectors/registry.py` — detector registry.
- `backend/app/services/dlp/detectors/presidio_ner.py` — NER detector + spaCy gate.
- `backend/app/services/guardrail_service.py` — regex blocklist.
- `backend/app/services/moderation_service.py` — OpenAI /v1/moderations client.
- `backend/app/services/semantic_router.py` — optional semantic routing.
- `backend/app/services/keyword_router.py` — keyword model hint.
- `backend/app/services/routing_service.py` — model/provider selection.
- `backend/app/services/rate_limit_service.py` — Redis sliding window.
- `backend/app/services/cache_service.py` — response cache.
- `backend/app/services/provider_dispatcher.py` — 14-adapter dispatch + fallback.
- `backend/app/services/providers/http_client.py` — per-call httpx.AsyncClient factory.
- `backend/app/middleware/metrics.py` — in-memory counter middleware.
- `backend/app/middleware/request_timing.py` — per-request timing log.
- `backend/app/middleware/timeout.py` — 20s timeout (skipped for /v1/chat/completions).
- `backend/app/core/db.py` — SQLAlchemy pool 10+20, recycle 300s.
- `backend/app/core/logging.py` — structlog JSON.
- `backend/app/core/config.py` — settings.

---

End of plan.
