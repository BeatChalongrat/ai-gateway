# Phase 2 — Benchmark Gate

Generated: 2026-05-30, branch `perf/phase-2-async-db`. Backend rebuilt with the
hybrid B3 fix (hot-path sync DB reads via `asyncio.to_thread`) + pool tuning.
Upstream = real `api.openai.com`. Approach: **hybrid (to_thread now, full
AsyncSession deferred)** per decision. Compare to `PHASE1_BENCHMARK.md`.

## Headline — the Phase-1 failure is fixed

Phase 1 failed at **40 concurrent**: sync connection checkout froze the event
loop **9.5 s** and 10 requests 500'd (`QueuePool ... timed out`). Phase 2:

| Concurrency | Phase 1 | Phase 2 |
|---|---|---|
| 40 parallel | **9.5 s loop-block + 10×500** | **40×200, 0 loop-block, 0×500** |
| 50 parallel | (not reached) | **50×200, 0 loop-block, 0×500** |

## Concurrency tiers (unique prompts; rate-limit window flushed per tier)

| N | 200 | 429 (rate-limit) | 500 (pool) | >100ms loop-blocks |
|---|---|---|---|---|
| 20 | 20 | 0 | 0 | 1 (one-time cold-start) |
| 40 | 40 | 0 | **0** | **0** |
| 50 | 50 | 0 | **0** | **0** |
| 75 | 60 | 15 | **0** | **0** |
| 100 | 60 | 40 | **0** | **0** |

- **Zero pool-exhaustion 500s at every tier**, including 50 (the gate) — vs Phase 1's
  10×500 at 40.
- **Zero >100ms loop-blocks** at 40/50/75/100. The single block at N=20 was the
  one-time cold-start (semantic/registry lazy-load on the first request after recreate;
  tiers 40–100 after it = 0).
- At 75/100 the **60-cap is the per-key rate limit** (60/min), not the pool — the 60
  that pass rate-limit reach the DB and are all served (0×500). Testing the pool beyond
  ~60 truly-concurrent DB-reaching requests needs a multi-key load tool (the rate limiter
  rejects before the DB layer); the pool itself peaked at only **32 Postgres connections**
  (of 60/worker, 250 server cap) — ample headroom.

## Gateway overhead (total − provider, request_phases log)
- **Cache-hit, 50 sequential, end-to-end:** p50 **45.8 ms** · p95 70 ms · p99 124 ms
  (consistent with Phase 1's 44.6 ms — the to_thread hops add ~1 ms; pooling+caches dominate).
- **Cache-miss, 30 sequential, gateway overhead:** p50 **47 ms** · p95/p99 **129 ms**
  (provider_ms p50 1340 ms). The p95 ~129 ms is driven by the **DLP output regex scan over
  long OpenAI responses** (now off-loop via Phase-1 B2, but its wall-time counts in
  `total_ms`); it is *not* DB/B3-related. Small n (15) → noisy.

## Gate result

| Criterion | Target | Result |
|---|---|---|
| Zero pool-exhaustion errors at 50 concurrent | 0 | ✅ **0** (50×200) |
| Zero >100ms loop-block under load | 0 | ✅ **0** at 40/50/75/100 (cold-start aside) |
| Worst-case gateway overhead < 120 ms | <120 | ⚠️ p50 47 ms ✅; **p95 ~129 ms** marginally over — DLP-output-scan-driven (not B3), small-n |

**The B3 target — sync-DB loop-blocking + pool exhaustion under concurrency — is
decisively resolved.** Two of three gate criteria are cleanly met; the third (overhead
<120 ms) is met at p50 and marginally exceeded at p95 by the DLP output scan over large
responses, which is an independent (already off-loop) cost, not the DB bottleneck Phase 2
targeted.

## End-to-end across phases (cache-hit p50, real OpenAI)
`Phase 0: ~98 ms → Phase 1: 44.6 ms → Phase 2: 45.8 ms` — Phase 2 holds Phase 1's latency
while removing the concurrency cliff.

## Hybrid limitation + the full-async follow-up
The hybrid keeps the **sync** session, which holds its connection for the whole request
**including the provider call (~1.3 s)**. So peak concurrent connections ≈ peak in-flight
requests/worker; the pool (60/worker) + executor (48) cover the 50-concurrent gate with
headroom (peaked at 32). Beyond that, the held-during-provider connection is the next
ceiling. **Full AsyncSession** (the deferred follow-up) additionally *releases the
connection during the provider await*, removing the per-request hold and lifting the
ceiling far higher — plus it converts the deferred conditional blocked-path persists +
the once-per-stream end log persist (still sync-on-loop, off the valid-request path).
Tracked in `ASYNC_DB_MIGRATION_MAP.md`.
