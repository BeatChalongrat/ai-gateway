# Capacity Validation — 150 / 250 Concurrent Streaming Chat

Status of the concurrent-streaming capacity work for the SRAN AI Gateway.
Tooling lives in [`tools/loadtest/`](../tools/loadtest/).

## TL;DR — what can / cannot be claimed

* ✅ **150 concurrent real-provider streams validated** (earlier run; see below):
  99.84% success, 0×429, async checkout timeouts = 0, async pool peak 16/35.
* ✅ **Memory leak found and fixed** — RSS now stays flat (~700 MiB) under
  sustained mock 150 *and* 250 (was climbing to ~11 GiB / OOM before).
* ⚠️ **250 concurrent remains UNDER VALIDATION.** A real-provider 250 run has
  **not** passed end-to-end yet — the most recent attempt was blocked by the
  **upstream provider's (OpenAI) rate limits**, not by the gateway. The
  gateway itself stayed healthy throughout (RSS bounded, async pool ≤17/35,
  aTO=0, no OOM). **Do not claim 250 support until a clean real-provider 250
  run passes.**

## Recommended prod-like config (validated for 150)

| Setting | Value |
|---|---|
| gunicorn workers | **4** (`UvicornWorker`, no `--reload`) |
| `DB_ASYNC_POOL_SIZE` | **15** |
| `DB_ASYNC_MAX_OVERFLOW` | **20** (→ 35/worker; 4×35 + sync 4×10 = 180 < PG `max_connections=250`) |
| `GUNICORN_MAX_REQUESTS` | **0** (worker recycling OFF — it interrupts SSE streams in waves; see below) |
| `INTERNAL_SERVICE_SECRET` | set on backend **and** chat-edge → trusted `X-User-ID` per-user rate limiting |
| per-user rate limit | 60/min (unchanged); project/tenant raised during load tests only |
| NER | off/default unless the tenant config requires it |

## 1. Rate-limit fix status

Shared web-chat `GATEWAY_API_KEY` no longer collides: trusted traffic sends
`X-User-ID` + `X-Internal-Service-Secret`, and the backend keys the limiter on
`api_key.key_id:user:<x_user_id>`. **429 = 0 across every load stage** (150 and
250, mock and real). Spoofed `X-User-ID` without the secret correctly falls
back to the bare key. Verified by preflight on every run.

## 2. Memory leak — root cause & fix

* **Root cause:** `ner_service_client.analyze_sync` runs the async NER call on
  a throwaway event loop via `asyncio.run()` (one per request when NER is on).
  `analyze` cached an `httpx.AsyncClient` in the module-global
  `_clients_by_loop[id(loop)]` and never closed it; the client's transport
  **pinned the dead loop**, so the loop's id was never reused and the dict grew
  one (dead-loop + client) pair per request → ~2 GiB / 90s at 100 concurrent.
* **Fix:** close + evict the per-loop client in a `finally` before
  `asyncio.run()` disposes the loop. NER behaviour is unchanged.
* **Evidence (tracemalloc, steady-state `idle→idle` diff):** **57.4 MiB → 0.6
  MiB** per burst cycle; the dominant `asyncio.run`/uvloop entry disappeared.
  Under sustained mock 150/250, **RSS is now flat ~700 MiB** (was ~11 GiB/OOM).

## 3. Async DB pool

* The async-pool saturation seen under the **mock** provider is a **mock
  artifact**: the instant mock never releases the DB connection during a
  provider call, so it pins the pool. The **real** provider releases the
  connection during its multi-second call, so the pool stays low.
* Real 150: async pool peak **16/35, aTO=0**. PG connection budget ≈ 180
  (4 workers × 45) + Go gateway 10 ≈ **~190 < 250** → **PgBouncer not needed**.

## 4. Results

### Real-provider 150 — VALIDATED (earlier run, gpt-4o-mini, streaming)
| Conc | Reqs | Success | 429 | 5xx | aTO | pool peak | TTFT p50/p95 |
|---|---|---|---|---|---|---|---|
| 50 | 1228 | 100% | 0 | 0 | 0 | 1/35 | 1.13/1.78 s |
| 100 | 3424 | 99.88% | 0 | 0.09% | 0 | 8/35 | 1.07/2.84 s |
| 150 | 5576 | 99.84% | 0 | 0.16% | 0 | 16/35 | 1.90/4.70 s |

### Mock-provider 150 / 250 after leak fix — memory-stable
| Conc | Success | 429 | 5xx | aTO | RSS |
|---|---|---|---|---|---|
| 150 | 99.61% | 0 | 0.39% | 0 | flat ~697 MiB |
| 250 | 99.38% | 0 | 0.62% | ~3–5 | flat ~747 MiB (no OOM) |

Mock-250 does not *strictly* pass (instant-mock pins the pool + mock self-500s),
but the **memory blocker is resolved** and there is **no OOM**.

### Real-provider 250 — NOT validated (provider-limited)
A staged real-250 attempt (150 gate → 250) **failed at the 150 gate with
~16% "5xx"**. Diagnosis: those were **OpenAI rate-limit errors** surfaced as
SSE error frames — backend logs showed `Too Many Requests` / `429` /
`all_models_failed` (only one provider configured, so the fallback chain had
nowhere to go). The **gateway stayed healthy**: gateway `/v1` HTTP 5xx counter
did not move, RSS stayed ~720 MiB, async pool ≤17/35, **aTO=0, no OOM**. This is
an **upstream provider rate limit**, not a gateway capacity failure.

## 5. What can be claimed / what cannot

* **Can claim:** 150 concurrent real-provider streams; the rate-limit per-user
  keying; the memory-leak fix; bounded RSS at 250 (mock).
* **Cannot claim:** 250 concurrent real-provider support — **not yet validated**.

## 6. Next risks / follow-ups

1. **Re-run real-250 with adequate provider headroom** — raise the OpenAI
   account rate limits (RPM/TPM) for gpt-4o-mini, or run against an account/
   tier that can sustain ~250 concurrent streams (~2500+ RPM), or add a second
   provider so the fallback chain absorbs upstream 429s. Until then real-250
   cannot pass regardless of gateway health.
2. **Mock-250 pool pinning** is a mock artifact (real provider does not
   reproduce it) — do not tune to it.
3. **Worker recycling (`--max-requests`)** is *not* a safe SSE memory
   mitigation (it interrupts in-flight streams in waves); the leak fix removes
   the need for it. Keep `GUNICORN_MAX_REQUESTS=0` for the streaming path.
4. **NER per-request `asyncio.run`** still spins a fresh loop per call (leak is
   fixed, but a persistent NER-bridge loop would cut CPU churn) — optional.

## 7. How to reproduce

See [`docs/LOAD_TEST_GROWTH_BASELINE.md`](LOAD_TEST_GROWTH_BASELINE.md) for the
tooling, overlays, preflight, and monitoring. Real-provider activation uses the
opt-in `tools/loadtest/docker-compose.prodlike-real.yml` overlay; always revert
to dev with `docker compose -f docker-compose.yml -f docker-compose.override.yml up -d backend`.
