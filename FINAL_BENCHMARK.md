# Track A — Final Benchmark (post AsyncSession flip + A.5 pool tuning)

**Date:** 2026-05-31
**Setup:** live stack, **real OpenAI** upstream, A.5-tuned pools
(async `10/15`, sync `5/5`; executor 20). Multi-key (8) load to push past
the 60/min per-key rate cap. `pg_stat_activity` sampled during each tier.

## Concurrency tiers

| concurrent | 200 | 429 (rate) | err | p50 ms | p95 ms | p99 ms | peak active conns | peak idle-in-txn |
|---|---|---|---|---|---|---|---|---|
| 20 | 10 | 0 | 10 | 14541 | 14546 | 14546 | 1 | 20 |
| 40 | 40 | 0 | 0 | 1576 | 2465 | 2502 | 1 | 20 |
| 50 | 50 | 0 | 0 | 2081 | 3330 | 3349 | 1 | 20 |
| 75 | 75 | 0 | 0 | 3067 | 4801 | 4822 | 2 | 20 |
| 100 | 100 | 0 | 0 | 3716 | 6028 | 6201 | 2 | 20 |
| 150 | 25 | 125 | 0 | 1517 | 2274 | 2282 | 2 | 20 |
| 200 | 0 | 200 | 0 | — | — | — | 2 | 19 |

(latency is end-to-end incl. the real-OpenAI call; the gateway adds ~40-60ms
overhead per the Phase-1/2 `request_phases` measurements, unchanged by the flip.)

## Reading the results

**H1 (connection release) — CONFIRMED, two independent ways:**
1. **100 concurrent → 100/100 success, 0 errors, on an async pool of only 25
   (10+15)**, with peak **active** DB connections = **2**. This is impossible
   if a connection were held through the ~2-6s provider call — 100 held
   connections on a 25-slot pool would exhaust it and ~75 would `pool_timeout`
   → errors. Zero errors ⇒ connections are committed/released before dispatch
   and **reused** across many in-flight requests.
2. Direct sampling during the **provider phase** of a 20-concurrent burst
   (earlier manual check): `idle in transaction` = **0** (all 20 requests in
   their OpenAI call held no connection).

**`peak idle-in-txn` ≈ 20 was a real held-connection bottleneck — on the AUTH
path, now fixed (see below).** It was the **auth dependency** holding a *sync*
connection for the whole request (the same H1 pattern the chat flip fixed, but
on `get_resolved_api_key`), plus the brief pre-dispatch read phase. After the
auth fix it is **0 during a 40-concurrent burst** (active=1, idle=16, total=17).

**NEW bottleneck found + fixed — auth connection-hold:** `get_resolved_api_key`
used `Depends(get_db_session)` (sync), held by FastAPI's yield-dep through the
ENTIRE request incl. the provider call. A.5's small sync pool (5/5) exposed it:
a **cold 50-concurrent burst → 40×500** (`QueuePool limit of size 5 overflow 5
reached`). Fix: verify the key on a **short-lived sync session that closes
immediately after verify_key** (releases the connection right after auth, not
held through the request) — mirroring the chat session's release. Re-test:
**cold 50-concurrent burst → 50×200, 0 errors**; 40-burst `idle-in-txn = 0`.

**Ceiling:** not reached at 100 (clean, 0 err). 150/200 are **rate-limit
capped** (429), not pool/loop limited — even at 200 concurrent the pool stayed
calm (active 2, **0 server errors**). The true pool/loop ceiling is above 100
and is gated by the per-key rate limit, not connections; measuring it precisely
needs the rate limit raised for the test.

**Cold-start: two causes, both fixed.** (1) the **lazy** async-engine build +
cold pool fill (thundering herd) — fixed by a **startup pre-warm** (build the
async engine + one pooled connection in lifespan before serving); (2) the held
auth sync connection above. Combined fix confirmed: **cold first burst of 50
concurrent → 50×200, 0 errors** (was 40×500 / 10×err-14s).

## Cross-phase comparison

| Metric | Before | Phase 1 | Phase 2 | **Final (Track A)** |
|---|---|---|---|---|
| Chat p50 (cache-hit) | 98ms | 44.6ms | 47ms | ~47ms (overhead unchanged) |
| Max concurrent, 0 server-err | 40 → crash | 40 → crash | 100 (to_thread) | **≥100 clean; ceiling now rate-limit, not pool** |
| Connection hold during provider call | ~1-3s | ~1-3s | ~1-3s | **~0 (released; held only ~10-30ms for reads)** |
| Peak active DB conns @ 100 concurrent | n/a | n/a | ~32 | **2** |
| Async pool needed for 100 concurrent | n/a | n/a | 60+ (held) | **25 (10+15), reused** |
| Per-worker pool budget | tight | tight | 60 | **35** (sync 10 + async 25) → ×4 = 140 << 250 |

**Capacity:** the per-request connection-hold ceiling is gone. Concurrency is
now bounded by the event loop + provider latency + the rate limiter, not the DB
pool. A 25-slot async pool/worker sustained 100 concurrent real-OpenAI requests
with 0 errors and 2 active connections; horizontal scaling (more workers) and
raising per-key rate limits extend it further, and the connection budget (140
of 250) leaves ample headroom.

## Gates

- 100 concurrent: **0 errors** ✅
- Connection release at scale: **confirmed** (100/0-err on a 25-pool; active=2) ✅
- 150/200: behavior documented — **rate-limit capped, 0 server errors** ✅
- Cold-start: identified + fixed (startup pre-warm) ✅

## Remaining (optional)

- Raise the per-key rate limit (or use more keys) to measure the true
  pool/loop ceiling beyond 100.
- Sustained soak (50 RPS × 5-10 min) for leak detection — connection count at
  rest is already clean (0 idle-in-txn, pool idle); a longer soak would confirm
  no slow leak over time.
