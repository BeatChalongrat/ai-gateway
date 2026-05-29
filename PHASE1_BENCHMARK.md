# Phase 1 — Benchmark Gate

Generated: 2026-05-30, branch `perf/phase-1-hotpath`. Stack: local Docker compose,
backend rebuilt with all Phase-1 commits. Upstream = real `api.openai.com` (gpt-4o-mini).
Compare to `PHASE0_BENCHMARK.md`.

## Headline

| Metric | Phase 0 | Phase 1 | Δ |
|---|---|---|---|
| Chat end-to-end p50 (cache-hit path, identical prompts) | ~98 ms | **44.6 ms** | **−54%** |
| 50 sequential requests, wall time | 17 s | **9 s** | −47% |
| Gateway overhead p50 (cache-hit) | — | **39 ms** | new measure |
| Gateway overhead p50 (cache-miss) | — | **59 ms** | new measure |

"Gateway overhead" = `total_ms − provider_ms` from the backend's `request_phases`
log (the authoritative non-provider cost). Both benchmarks are cache-hit-dominated
on the sequential identical-prompt run (caching is on), so the 98→44 ms comparison
is apples-to-apples.

## Method
- Backend `request_phases` structured log gives per-request `auth_ms / rate_limit_ms /
  routing_ms / dlp_regex_ms / cache_ms / provider_ms / total_ms`.
- Warm runs only (cold first request — model/registry load — excluded).
- Rate-limit window flushed before each run. Client `time_total` via curl.

## Gateway overhead (total − provider)

| Path | p50 | p95 | p99 |
|---|---|---|---|
| Cache-hit (provider_ms=0) | **39 ms** | 69 ms | 105 ms |
| Cache-miss (real provider) | **59 ms** | 132 ms | 132 ms |

Per-phase medians (cache-hit path): `auth 2ms · rate_limit 2ms · routing 0ms ·
dlp_regex 19ms · cache 1ms`. (Phase-0 warm cold-start showed `rate_limit 252ms`,
`dlp` higher — those are first-call/cold; warm steady-state is now single-digit
except the DLP regex scan, which is now **off the event loop** via to_thread.)

Cache-miss end-to-end p50 = **1324 ms**, entirely provider-bound (`provider_ms`
p50 = 1381 ms for the "reply briefly" prompts); gateway overhead is 59 ms of that.

## What moved the numbers (Sessions 1.1–1.6)
- **B1 provider pooling** — the dominant end-to-end win: every request used to pay a
  fresh DNS+TCP+TLS handshake to OpenAI; now 1 pooled connection is reused (verified:
  15 requests → 1 ESTABLISHED :443 socket).
- **B6 alert offload** — removed a per-request enabled-rules SELECT + window aggregates
  (+ a streaming get_by_id read-back) from the loop; now a 10 s background sweep.
- **B5/B7/B8 caching** — removed per-request DB round-trips (provider-key SELECT,
  keyword-rule SELECT) and the per-request `last_used_at` WRITE (debounced 5 min).
- **B2 DLP / B4 semantic** — moved the regex scan (≈19 ms) and the MiniLM encode off
  the event loop (to_thread + Semaphore), so they no longer serialize other coroutines.
- **B9 orjson / B12 cache** — faster response encode + 1 awaited Redis round-trip on cache GET.

## Loop-block under concurrency

| Concurrency | Pool (30) | Result | >100ms loop-blocks |
|---|---|---|---|
| 20 parallel | under | 20×200 | **0 (clean)** |
| 40 parallel | **over** | 30×200 + **10×500** | **1 × 9.5 s** |

At **20 concurrent (below the 30-connection pool)** the event loop is clean — Phase 1
removed the DLP/semantic/serialization blocks. At **40 concurrent (above the pool)**
the backend logged a **9.5 s loop-block** and 10 requests 500'd with
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached`.

**Root cause:** the request path still issues **synchronous Postgres reads on the event
loop** that Phase 1 did NOT cache (project-context resolve, quota, budget, policy-profile,
provider-context). Once in-flight concurrency exceeds the pool (30), a sync connection
*checkout* blocks the **whole event loop** for up to `pool_timeout=10s` → the 9.5 s block
+ pool-timeout 500s. This is bottleneck **B3 (async SQLAlchemy)** — explicitly **Phase 2**
in `PERFORMANCE_AUDIT_REPORT.md`. Phase 1's in-place fixes cannot remove it: only an async
engine (await on checkout instead of blocking; release the connection during the provider
await) eliminates the loop-block and lifts the effective concurrency ceiling.

## Gate result

| Gate criterion | Status |
|---|---|
| Typical-case gateway overhead < 80 ms | ✅ **MET** — cache-hit p50 39 ms, cache-miss p50 59 ms |
| No >100 ms loop-block under 50 RPS soak | ❌ **NOT met** above the DB pool size — blocked by remaining sync Postgres reads (B3) |

**Conclusion.** Phase 1 hit the latency/overhead target decisively (p50 98→44 ms;
DLP/semantic/serialization off the loop; clean under concurrency ≤ pool size) and
**pinpointed the one remaining hot-path bottleneck**: synchronous Postgres on the event
loop (B3). The loop-block-under-load gate requires **Phase 2 (async DB)** — exactly the
audit's sequencing. Interim mitigation: raising `DB_POOL_SIZE`/`max_overflow` lifts the
concurrency at which the block appears but does not remove the sync-checkout-on-loop
behaviour; async DB is the real fix.

*Caveat (carried from Phase 0): a true 50-RPS-for-5-min soak needs a multi-key load
generator (single key caps at 60/min); the concurrency bursts above are the proxy and
already surface the B3 limit.*
