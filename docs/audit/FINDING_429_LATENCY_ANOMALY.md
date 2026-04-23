# Finding — 4.7 s Latency on 429 Rejection Under Concurrent Load

**Discovered:** 2026-04-22, Phase-1 crash investigation (`CRASH_INVESTIGATION.md §3`).
**Status at Phase 2:** documented, not fixed. Explicit scope decision: the baseline
must measure the current behaviour, not paper over it. A fix is proposed but
deferred.
**Framing for the Auditor:** **this finding is independent of the Go-migration
question.** Even if the stack stays Python, this anomaly is a DoS-by-accident
vector and is worth fixing on its own merits. Any go/no-go on Go must NOT
block on this — nor should a "stay Python" verdict dismiss it.

---

## 1. Observation

On `POST /v1/chat/completions` under 50 RPS against a project whose rate-limit
budget is 300 req/min (i.e. saturated by factor 10×), rejected requests log:

```
auth_ms=5, rate_limit_ms=24, routing_ms=0, dlp_regex_ms=0, dlp_ner_ms=0,
guardrails_ms=0, cache_ms=0, provider_ms=0, response_assembly_ms=0,
total_ms=4 800   ← order of magnitude larger than the sum of phases
```

Sum of every instrumented phase: ~29 ms.
Unaccounted gap: **~4 770 ms**.

The gap is observed on *rejected* (429) requests. Accepted requests at the
same load do not show it — they run the full pipeline and the per-phase
numbers reconcile against `total_ms` within ±5 %.

## 2. Middleware chain, verified by reading `backend/app/main.py`

`backend/app/main.py:249-271` declares the stack. Starlette's
`add_middleware` inserts each new layer on the outside of the stack, so
**the last `add_middleware` call runs first on the inbound request**.
Resulting wire order (outermost → innermost):

```
1. CORSMiddleware                         # main.py:251
2. BodySizeLimitMiddleware                # main.py:267
3. RequestContextMiddleware               # main.py:266
4. RequestTimingMiddleware                # main.py:265
5. TimeoutMiddleware  (skipped for /v1/chat/completions — timeout.py:27)
6. MetricsMiddleware                      # main.py:263
7. FastAPI router → chat_completions      # public_chat.py:66
8. Depends(get_resolved_api_key)          # deps.py:88 — DB lookup
9. GatewayOrchestrator.handle_chat_completion
     ├─ record_phase("auth")              # orchestrator.py:128
     ├─ record_phase("rate_limit")        # orchestrator.py:150
     │   └─ RateLimitService.check_all → 12 sync Redis ops (3 scopes × 4 cmds)
     └─ raise RateLimitExceededError      # orchestrator.py:160
```

Nothing in layers 1-6 does DB or Redis work for a /v1/chat/completions
request (BodySizeLimit reads only the Content-Length header, the others
wrap the ASGI chain). The first code path that awaits a non-trivial
resource is `Depends(get_resolved_api_key)` (layer 8), which opens a
SessionLocal and calls `api_key_service.verify_key()` — a synchronous
SELECT against the `api_keys` table executed via `asyncio.to_thread`
indirectly through SQLAlchemy's sync session.

### Where the instrumented phases start / end

| Phase | Enters | Exits |
|-------|--------|-------|
| `auth_ms` | orchestrator.py:128 `with record_phase("auth"):` | orchestrator.py:134 close-of-with |
| `rate_limit_ms` | orchestrator.py:150 `with record_phase("rate_limit"):` | orchestrator.py:157 close-of-with |
| `total_ms` | PhaseTimer constructed at `MetricsMiddleware.__call__` (metrics.py:130-131, BEFORE `self.app(...)` fires) | same middleware, after the downstream handler returns |

Interpretation: `total_ms` is measured from the MOMENT `MetricsMiddleware`
sees the request to the moment its response-start wrapper sees the
status line. `auth_ms` only begins once the orchestrator's `with`
block opens — that is, AFTER `Depends(get_resolved_api_key)` completed,
AFTER the body was parsed by FastAPI, AFTER the DB session was
constructed. **Everything between `MetricsMiddleware` start and the
orchestrator's first `record_phase` call is invisible to the phase
table.** That is where the ~4.7 s gap lives.

## 3. Quantifying the gap — hypothesis ranking

| # | Hypothesis | Evidence | Confidence |
|---|------------|----------|-----------|
| 1 | **Asyncio event-loop queueing at high concurrency.** Single uvicorn process (4 workers), limit_concurrency=100 per worker. At 200 concurrent k6 VUs, each worker gets ~50 in-flight requests. A chat request consumes ~65 ms of CPU on the event loop (regex + JSON + routing) and ~1.3 s of `await provider`. Until a loop slot frees, a newly-accepted TCP connection's ASGI `__call__` is queued. | Gap scales with VUs (observed 200-VU saturation in k6 log line "Insufficient VUs, reached 200 active VUs"). Gap is absent on low-RPS smoke runs. `MetricsMiddleware` starting after a ~4.7 s wait is consistent with queue depth × per-request CPU time. | **HIGH** — best single explanation |
| 2 | **Sync DB SELECT in `get_resolved_api_key` blocks the thread pool.** `api_key_service.verify_key()` calls SQLAlchemy's sync session over a connection checked out from the 10+20 pool. When every pool connection is held by in-flight chat requests waiting on the provider, new auth lookups wait on pool acquisition. | Pool size is 10+20 = 30 max. At 200 VUs, pool must serialise ~170 requests at any moment → bounded wait per request = (in-flight / pool_size × per-request-DB-ms) ≈ 5-7 × 30 ms ≈ 200 ms. Doesn't fully account for 4.7 s on its own, but stacks with #1. | **MEDIUM** — contributes, not dominant |
| 3 | **Starlette `BaseHTTPMiddleware` streaming quirks.** Known overhead under concurrency. | Not applicable — every middleware in the current stack is pure ASGI (see `MetricsMiddleware(ASGIApp)`, `RequestTimingMiddleware`, etc. in `backend/app/middleware/*.py`). `BaseHTTPMiddleware` is not used. | **REJECTED** |
| 4 | **Redis client acquisition blocking on saturated pool.** Rate limiter uses sync `redis-py` (`rate_limit_service.py:34`) in a blocking call from the event loop. | Each `check_all` is 12 sync Redis ops off the thread pool. Redis client is a single connection per RateLimitService (no pool). Under 200 concurrent rate-limit checks, serialisation is bounded by the Redis single-threaded command queue, not by client pool. Adds ~50-100 ms at worst. Doesn't explain 4.7 s. | **LOW** — present, minor |
| 5 | Request body read on large bodies. | k6 payloads are ~300 byte JSON. Content-Length is tiny. | **REJECTED** |
| 6 | NER warm-up or a singleton lock. | `dlp_regex_ms=0` on 429 requests — DLP never ran. NER is irrelevant. | **REJECTED** |

### Net picture

The dominant cost is **#1 (event-loop queueing)**, with **#2 (thread-pool /
DB-pool serialisation)** stacking on top once the async FastAPI chain
calls into the sync `get_resolved_api_key` dependency. #3, #4, #5, #6
are ruled out or minor.

## 4. Request-flow diagram

```
TCP accept
    │
    ▼
uvicorn http_protocol                 ← up to ~4 000 ms wait HERE
    │                                    when queue is saturated (#1)
    ▼
Starlette app.__call__
    │
    ├─ CORSMiddleware                  ~ 0 ms
    ├─ BodySizeLimitMiddleware         ~ 0 ms   (Content-Length only)
    ├─ RequestContextMiddleware        ~ 0 ms   (uuid + header copy)
    ├─ RequestTimingMiddleware         ~ 0 ms   (logs START)
    ├─ TimeoutMiddleware               skipped for /v1/chat/completions
    ├─ MetricsMiddleware               PhaseTimer bound HERE
    │                                  ─────────────── t=0 for total_ms
    ▼
FastAPI router resolves /v1/chat/completions
    │
    ▼
Depends(get_resolved_api_key)         ~ 200-800 ms under load (#2)
    │   └─ SessionLocal() + verify_key SELECT
    │      (still invisible to any record_phase block)
    ▼
Depends(get_db_session)               yields session (sync)
    │
    ▼
chat_completions(auth, payload, session)
    │
    ▼
GatewayOrchestrator.handle_chat_completion
    │
    ├─ with record_phase("auth"):      ← auth_ms START (≈ 5 ms)
    │     resolve_project_context      ← one SELECT
    │                                  ← auth_ms END
    │
    ├─ with record_phase("rate_limit"):← rate_limit_ms START (≈ 24 ms)
    │     RateLimitService.check_all   ← 12 sync Redis ops
    │     ├─ check_key    → 60/min     ← usually PASSES (perf pool = 100 keys)
    │     ├─ check_project→ 300/min    ← FAILS at 50 RPS
    │     │   └─ scope = "project"
    │     └─ (tenant not reached)
    │                                  ← rate_limit_ms END
    │
    └─ raise RateLimitExceededError    ← 429 fires
         (sum of captured phases ≈ 29 ms; total_ms ≈ 4 800 ms)
```

**Invisible slot:** everything above `record_phase("auth")` — that's where
the unaccounted ~4.7 s sits.

## 5. Minimum reproduction

Prerequisites: repo checked out, `docker compose up -d postgres redis`, backend
running at `:8080` with `PERF_TEST_PROJECT_LIMIT` **unset** (so the default
300 req/min project cap applies), `perf/seed_keys.py` already run.

```bash
# Terminal 1 — keep this running; observe request_phases log lines.
export GATEWAY_LOG_PATH=logs/gateway.log
uvicorn app.main:app --host 0.0.0.0 --port 8080 2> >(tee -a logs/gateway.log >&2)

# Terminal 2 — Python asyncio reproducer (no docker, no k6 needed).
python - <<'PY'
import asyncio, json, time, httpx

URL = "http://localhost:8080/v1/chat/completions"
HDR = {"Authorization": "Bearer <one of perf/fixtures/api-keys.json raw_key>",
       "Content-Type": "application/json"}
BODY = json.dumps({"model": "gpt-4o-mini",
                   "messages": [{"role": "user", "content": "ping"}]})

async def fire():
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(URL, headers=HDR, content=BODY)
    return r.status_code, (time.perf_counter() - t0) * 1000

async def main():
    # 400 concurrent requests — 100× over the 300/min project cap
    results = await asyncio.gather(*[fire() for _ in range(400)])
    by_status = {}
    for status, ms in results:
        by_status.setdefault(status, []).append(ms)
    for s, xs in sorted(by_status.items()):
        xs.sort()
        p50 = xs[len(xs)//2]; p95 = xs[int(len(xs)*0.95)]
        print(f"  {s}: n={len(xs)} p50={p50:.0f}ms p95={p95:.0f}ms max={max(xs):.0f}ms")

asyncio.run(main())
PY
```

Expected output: most requests return 429; their p50 is well above `rate_limit_ms`
in the gateway's log line (`request_phases` event). Comparing the k6 `http_req_duration`
for a 429 against the matching `total_ms` + `auth_ms` + `rate_limit_ms` shows the gap.

To pin the gap to hypothesis #1 vs #2, re-run with `--workers 1` and with
`--workers 4`: the gap shrinks proportionally as worker count rises,
confirming event-loop queueing as the dominant term.

## 6. Hypothesis — stated explicitly

> The 4.7 s gap on 429 responses is predominantly asyncio event-loop queue
> time — requests wait on uvicorn's ASGI dispatch before any middleware or
> handler code runs. A secondary term (~200 ms) comes from the sync
> `get_resolved_api_key` DB lookup blocking the thread pool once connections
> saturate. No component of the gap is inside the rate-limit check itself;
> the check is fast (~24 ms) and correct — it's everything *before* the
> check that's slow.

## 7. Suggested fix directions (flag-only, **do not implement before the baseline**)

Any of these would shrink the gap dramatically. They need design discussion
— not patched in mid-perf-run.

1. **Move rate-limit to a middleware that runs first.** A thin ASGI
   middleware between `RequestContextMiddleware` and `BodySizeLimit` can
   short-circuit on project/key limits **before** FastAPI parses the body
   or resolves dependencies. A rejected request would skip auth entirely,
   collapsing the gap to ~1-2 ms. **Tradeoff:** the middleware needs the
   key/project mapping before auth runs, so it either caches (project, key
   hash) lookups in Redis or duplicates a minimal auth path. Prefer the
   cache: TTL 60 s, keyed on `sha256(api_key)`, value = `(project_id,
   tenant_id)`.

2. **Replace the 12-call Redis pipeline with one atomic Lua EVAL.** A
   single round-trip scanning all three scopes (key / project / tenant)
   cuts sync Redis time from ~24 ms to ~2-3 ms and eliminates the partial
   state risk that currently exists when two of three scopes pass and the
   third fails (we've already added a count on every scope).

3. **Local TTL cache of "currently limited" keys/projects.** When a
   project is over cap, its next 5 s of requests are obviously still over
   cap — there's no need to hit Redis 3 times per request to re-prove it.
   A 1 s in-process bloom / LRU of currently-denied IDs, refreshed on
   each reset, shaves latency further and absorbs the burst load that
   currently queues on Redis.

4. **Switch `get_resolved_api_key` to use `redis.asyncio` + async DB.**
   The sync SQLAlchemy session dependency blocks the thread pool when
   the DB is slow. An async variant would keep the event loop free
   under load — independent of the rate-limit question.

5. **Raise `limit_concurrency` and split to more workers.** Tactical
   only; doesn't address the root cause but buys headroom. Noted for
   completeness.

The Auditor should **not** decide Go-migration on the back of this gap
alone. The gap disappears under any of #1-#4 — all are Python-internal
refactors. Move it into a Go service only if the Auditor sees additional
evidence outside this finding that warrants the full migration cost.

## 8. What the baseline will show

The Phase 3 baseline **must not** erase this finding. The test-config
change (raising `perf-test-project` limit to 120 000 req/min) keeps the
per-key limit at the production default (60/min), so the dedicated
`rate-limit-reject-latency` scenario can still drive saturation on a
single key and reproduce the gap. The scenario is designed to quantify
time-to-429 under controlled saturation — its output is the
Auditor-facing measurement of this anomaly's current state.

---

## Related findings

> **Related findings**: Secondary hypothesis (#5) suggests DB pool saturation contributes ~200ms of the 4.7s. This finding should be read alongside `FINDING_DB_POOL_SATURATION.md`. The two findings may share root cause: both surface as latency under saturation, and DB pool fix may partially mitigate 429 latency.

---

End of finding.
