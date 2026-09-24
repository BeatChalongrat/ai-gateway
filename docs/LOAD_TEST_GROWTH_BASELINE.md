# Load-Test Growth Baseline — SRAN AI Gateway

A controlled, staged load test of the **streaming chat** path, ramping
`25 → 50 → 100 → 150 → 250` concurrent users to find the real capacity of the
current stack. Tooling lives in [`tools/loadtest/`](../tools/loadtest/).

> This document is operational. It adds **no** production behaviour — only
> opt-in load-test tooling, an opt-in activation overlay, and these docs.

---

## 1. Goal

Establish a defensible capacity baseline for concurrent streaming chat users,
gradually, with clear pass/fail gates per stage. **Current target: 250
concurrent streams** (not 400). We do **not** claim a level is supported until
a stage at that concurrency actually passes.

## 2. Assumptions / what the harness does

* Traffic enters at the **Go gateway**: `POST http://localhost:8080/v1/chat/completions`
  (transparent SSE-safe reverse proxy → Python `backend:8080`). Verified from
  the running stack and `services/gateway/internal/proxy/proxy.go`.
* Auth is the same as `chat-edge`:
  * `Authorization: Bearer <GATEWAY_API_KEY>`
  * `X-User-ID: <unique per virtual user>` (`loadtest-user-000001`, …)
  * `X-Internal-Service-Secret: <secret>` **only when** per-user keying is
    activated — then the backend buckets each VU on
    `api_key.key_id:user:<x_user_id>` (its own 60/min window). Without it,
    every VU shares **one** key bucket.
* Each virtual user paces realistically (think-time + jitter), never a tight
  loop. Every request carries a unique nonce so the gateway **response cache**
  doesn't serve a cached body and flatter the numbers.
* The harness is **read-only** against the system (it only sends API traffic).

## 3. Required / relevant env vars (verified names)

| Var | Where | Purpose |
|---|---|---|
| `GATEWAY_API_KEY` | `.env` → chat-edge + load test | Shared Bearer key. **Set** (`gw_…`). |
| `INTERNAL_SERVICE_SECRET` | backend **and** chat-edge | Activates trusted `X-User-ID` per-user keying. **Currently UNSET on both** → keying inactive (see §4). |
| `RATE_LIMIT_KEY_PER_MINUTE` | backend | Per-user/key window. Default **60**. |
| `RATE_LIMIT_PROJECT_PER_MINUTE` | backend | Per-project window. Default **3000**. |
| `RATE_LIMIT_TENANT_PER_MINUTE` | backend | Per-tenant window. Default **10000**. |
| `PROVIDER_BASE_URL_OVERRIDES` | backend | JSON map to point adapters at the mock provider. **Empty** → real OpenAI. |
| `OPENAI_API_KEY` | backend | Real key is **set** → real-provider runs cost money. |
| `RATE_LIMITER_ENGINE` | backend | `rust` (live). Per-user `key_id` is passed through to the sidecar unchanged — **no sidecar change needed**. |
| `DLP_ENGINE` | backend | `rust` (live). |
| `NER_SERVICE_URL` | backend | NER runs as a dedicated service (`:8001`); whether a request uses NER is a tenant/DLP-profile setting (see §9). |

Defaults read live from the running backend:
`key/project/tenant = 60 / 3000 / 10000`, `image = 10/hr, 100/day`.

## 4. Activate `INTERNAL_SERVICE_SECRET` (per-user keying)

**Status: NOT active.** Neither the backend nor chat-edge has
`INTERNAL_SERVICE_SECRET` set (confirmed via `docker exec … env`). Until it is,
all load-test VUs collide on **one** shared-key 60/min bucket, which makes a
>60 req/min test meaningless (it just measures the rate limiter).

The code path is in place (`backend/app/api/deps.py::resolve_rate_limit_key_id`,
chat-edge `gateway_client.go` sends `X-Internal-Service-Secret`). Activation:

**Backend (gateway side)** — for load testing use the opt-in overlay (recreates
only the backend container; reverts cleanly):

```bash
INTERNAL_SERVICE_SECRET=<choose-a-secret> \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.override.yml \
  -f tools/loadtest/docker-compose.loadtest.yml \
  up -d backend
```

The overlay ([`tools/loadtest/docker-compose.loadtest.yml`](../tools/loadtest/docker-compose.loadtest.yml))
also (a) points adapters at the **mock provider** and (b) raises the
project/tenant ceilings so a 250-VU capacity probe isn't masked by them (the
per-user 60/min stays at the production value).

**chat-edge (production web-chat path)** — needed only for real web-chat
traffic, *not* for this harness (the harness sends the header itself). chat-edge
is a compiled Go image, so:
1. add `- INTERNAL_SERVICE_SECRET=<same-secret>` to the `chat-edge-api`
   service env in `docker-compose.webchat.yml`,
2. rebuild: `docker compose -p ai-gateway -f docker-compose.webchat.yml build chat-edge-api && … up -d chat-edge-api`.

**Revert the backend to normal afterwards:**

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d backend
```

## 5. Run preflight

```bash
export GATEWAY_API_KEY="$(grep -E '^GATEWAY_API_KEY=' .env | cut -d= -f2-)"

# Mock provider activated (recommended first):
python tools/loadtest/preflight.py --model gpt-4o-mini --assume-mock \
  --internal-service-secret "<secret>" --probe-rate-limit --probe-key-limit 60

# Real provider (acknowledge cost):
python tools/loadtest/preflight.py --model gpt-4o-mini --allow-real-provider
```

Preflight checks: env vars, gateway/backend/prometheus/ner reachability, one
real streaming request, and (opt-in `--probe-rate-limit`) that two trusted
`X-User-ID`s get **separate** counters and a spoofed `X-User-ID` (no secret)
falls back to the shared key. Exit 0 = safe to proceed.

> The rate-limit probe needs the per-key limit small to be cheap/observable.
> Temporarily set `RATE_LIMIT_KEY_PER_MINUTE=8` in the overlay and pass
> `--probe-key-limit 8`, then restore.

## 6. Run the stages

Single stage:

```bash
export GATEWAY_API_KEY="$(grep -E '^GATEWAY_API_KEY=' .env | cut -d= -f2-)"
python tools/loadtest/stream_chat_loadtest.py \
  --model gpt-4o-mini \
  --internal-service-secret "<secret>" \
  --concurrency 50 --duration-seconds 600 --ramp-seconds 30 \
  --payload-size-kb 2 --think-time-seconds 1 --jitter-seconds 1 \
  --stream true --output-json tools/loadtest/results/stage_50.json
```

Full staged suite (stops on first failing stage):

```bash
export GATEWAY_API_KEY="$(grep -E '^GATEWAY_API_KEY=' .env | cut -d= -f2-)"
export INTERNAL_SERVICE_SECRET="<secret>"

# Production-length stages (5/10/10/10/12 min):
bash tools/loadtest/run_growth_baseline.sh gpt-4o-mini

# Shortened dev run, cap at 150:
STAGE_DURATIONS="60 120 120 120 150" MAX_CONCURRENCY=150 \
  bash tools/loadtest/run_growth_baseline.sh gpt-4o-mini
```

Recommended order (per the task):
1. **NER off/default, mock provider, up to 150** — isolate the system path.
2. **Real `gpt-4o-mini`, up to 150** — add provider latency.
3. **Real `gpt-4o-mini`, up to 250** — target.
4. Only then consider **NER-on**.

Watch live in a second terminal:

```bash
INTERVAL=2 bash tools/loadtest/monitor.sh
```

## 7. Interpreting results

The script prints, and writes to `--output-json`:

* `total_requests`, `success_rate`, `requests_per_sec`
* `rate_limited_429_rate`, `server_5xx_rate`, timeouts, connection errors
* total request duration `p50/p95/p99/max`
* time-to-first-token `p50/p95/p99` (streaming)

Separate **system-side** latency from **provider** latency:
* With the **mock** provider, p95 should be low and stable → that is the
  system's own overhead (auth, rate-limit, DLP, routing, proxy).
* With the **real** provider, TTFT and total duration are dominated by OpenAI;
  report system-side (mock) and provider (real) numbers separately rather than
  conflating them.

## 8. Pass/fail criteria (per stage)

A stage **passes** when:
* `success_rate >= 99%`
* unexpected `429` near zero (`--max-429-rate`, default `0.01`)
* `5xx` near zero (`--max-5xx-rate`, default `0.005`)
* `db_pool_checkout_timeouts_total` delta **= 0** during the stage
* no container OOM / restart
* no obvious memory leak (RSS flat across stages in `docker stats`)
* p95 total duration stays reasonable for the model used

The script's **exit code** encodes success-rate / 429 / 5xx; the DB-timeout,
OOM, and leak checks are observed via §10 monitoring (not auto-failed by the
script).

## 9. Observability — what to watch

### Verified metric names

| Signal | Endpoint | Metric |
|---|---|---|
| gateway in-flight | `:8080/__gw/metrics` | `gateway_in_flight_requests` |
| gateway req totals | `:8080/__gw/metrics` | `gateway_requests_total{path_group,status}` |
| gateway latency | `:8080/__gw/metrics` | `gateway_request_duration_seconds` |
| backend in-flight | `:8081/metrics` | `http_requests_in_flight{route}` |
| backend latency | `:8081/metrics` | `http_request_duration_seconds{route,method,status}` |
| per-phase latency | `:8081/metrics` | `gateway_request_phase_duration_seconds{phase}` (auth, rate_limit, dlp_regex, dlp_ner, provider, …) |
| DB pool active | `:8081/metrics` | `db_pool_active` |
| DB pool overflow | `:8081/metrics` | `db_pool_overflow` |
| DB pool max | `:8081/metrics` | `db_pool_max` (sync pool = pool_size+overflow) |
| **DB checkout timeouts** | `:8081/metrics` | `db_pool_checkout_timeouts_total` |
| rate-limit fail-open | `:8081/metrics` | `rate_limit_fail_open_total` (Redis down → admitted, NOT limited) |
| sidecar fallback | `:8081/metrics` | `sidecar_fallback_total{engine}` (rate-limiter/dlp dropped to Python) |

> **DB pool note:** `db_pool_*` instruments the **sync** pool (`db_pool_max=10`
> = 5+5). The chat hot path uses a separate **async** pool (10+15, released
> during the provider call). So during a clean streaming run `db_pool_active`
> can stay near 0 — that is expected. A rising sync `db_pool_overflow` /
> `db_pool_checkout_timeouts_total` points at admin/portal/background or the
> rare sync chat touchpoints under load.

### docker stats

```bash
docker stats ai-gateway-backend-1 ai-gateway-gateway-1 ai-gateway-ner-service-1 \
  ai-gateway-postgres-1 ai-gateway-redis-1 ai-gateway-rate-limiter-1 ai-gateway-dlp-sidecar-1
```
The `monitor.sh` helper polls the metrics above on an interval. Add
`docker stats --no-stream` for CPU/RAM per container (watch `ner-service` RAM
when NER is on, and `backend` RSS for leaks).

### Prometheus (`http://localhost:9091`) — example PromQL

```promql
# gateway in-flight requests
gateway_in_flight_requests

# backend in-flight (sum across routes)
sum(http_requests_in_flight)

# p95 gateway request duration (5m)
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))

# p95 backend chat duration
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{route="/v1/chat/completions"}[5m])) by (le))

# 429 rate on /v1 (per-sec)
sum(rate(gateway_requests_total{path_group="/v1",status="429"}[1m]))

# 5xx rate on /v1 (per-sec)
sum(rate(gateway_requests_total{path_group="/v1",status=~"5.."}[1m]))

# DB checkout timeouts (must stay flat / zero)
increase(db_pool_checkout_timeouts_total[5m])

# rate-limiter sidecar falling back to Python
increase(sidecar_fallback_total{engine="rate_limiter"}[5m])
```

Redis errors: `docker logs ai-gateway-redis-1` and the `redis-exporter`
(`:9121`) `redis_*` series; watch evictions (`maxmemory-policy allkeys-lru`).

## 10. Known bottlenecks

1. **Single-worker dev backend** (until FIX131). The dev
   `docker-compose.override.yml` ran `uvicorn --reload` (one process). Since
   FIX131 the stack runs the image's gunicorn with `GUNICORN_WORKERS` workers
   like production; the one-process hot reload is the opt-in
   `docker-compose.dev.yml`.
   A single async worker handles many concurrent streams via the event loop,
   but CPU-bound work (DLP regex, JSON, and especially **NER**) serializes on
   it. **Dev capacity numbers are a floor, not the prod ceiling.**
2. **NER RAM/throughput.** `ner-service` is capped at 4 CPU / 4 GB. With NER on,
   spaCy holds the GIL during `analyze()`; sustained high concurrency drives its
   CPU and RAM up. Test NER-on **last**, watching its `docker stats`.
3. **Provider latency.** Real `gpt-4o-mini` TTFT dominates total duration and is
   outside our control — report it separately from system-side (mock) latency.
4. **Rate-limit activation.** With per-user keying inactive, all VUs share one
   60/min bucket → early 429s that are *not* a capacity signal. Activate the
   secret (§4) before any run above ~60 req/min.
5. **DB pool timeouts.** Sync pool is small (5+5). The async chat pool is 10+15
   per worker. Sustained checkout timeouts = the real ceiling for whichever
   pool saturates.
6. **Gateway proxy idle conns.** `MaxIdleConnsPerHost=100` limits *pooled idle*
   conns, not active streams; not expected to cap 250 active streams, but worth
   a glance if connection reuse churns.

## 11. Troubleshooting — what to do if…

* **429 appears early:** per-user keying is almost certainly inactive, or the
  per-key limit is being hit by a single VU. Confirm `INTERNAL_SERVICE_SECRET`
  is set on the backend *and* passed to the script; confirm each VU has a unique
  `X-User-ID` (default prefix does). If the project/tenant ceiling is the cause,
  use the overlay's raised limits. Distinguish *expected* 429 (production limit
  doing its job) from *unexpected* 429 (collision bug).
* **5xx appears:** check `docker logs ai-gateway-backend-1` and
  `gateway_request_phase_duration_seconds` to find the slow/failing phase
  (provider, dlp_ner, rate_limit). A burst of 502 at the gateway = backend
  unreachable / worker died. Check for OOM (`docker inspect … State.OOMKilled`).
* **DB checkout timeout (`db_pool_checkout_timeouts_total` rising):** a pool is
  saturated. Lower concurrency one stage, confirm it clears, then raise
  `DB_ASYNC_POOL_SIZE` / `DB_ASYNC_MAX_OVERFLOW` (and Postgres
  `max_connections`, currently 250) deliberately — not blindly.
* **NER memory approaches cap:** reduce concurrency, confirm NER is actually
  required for the tenant under test, and consider scaling `ner-service`
  (replicas / CPU). Re-baseline NER-on separately.
* **Provider timeout:** real upstream slow/over quota. Switch to the mock
  provider to prove the system path, and report provider latency separately.
  `--timeout-seconds` controls the client read timeout.
* **p95 latency spikes:** correlate with `gateway_request_phase_duration_seconds`
  to attribute to a phase; check `docker stats` for a pegged CPU (single-worker
  backend or NER) and for GC pauses on the Go gateway
  (`go_gc_duration_seconds`).

---

## Appendix A — files

| File | Purpose |
|---|---|
| `tools/loadtest/stream_chat_loadtest.py` | async streaming load generator + summary + exit code |
| `tools/loadtest/preflight.py` | env / reachability / single-stream / rate-limit-fix checks |
| `tools/loadtest/run_growth_baseline.sh` | staged 25→250 runner, stops on first failure |
| `tools/loadtest/monitor.sh` | live metrics dashboard (docker stats + Prometheus signals) |
| `tools/loadtest/docker-compose.loadtest.yml` | opt-in overlay: mock provider + secret + raised ceilings |
| `tools/loadtest/results/` | per-stage JSON output (gitignored) |

## Appendix B — current readiness (as of this baseline)

* Tooling + preflight + monitoring: **implemented and validated** end-to-end
  against the live stack (preflight green; a 5-VU/20s real-`gpt-4o-mini`
  harness-validation run: 100% success, 0×429, 0×5xx, TTFT p95 ≈ 2.4s).
* **150 concurrent: NOT yet verified** — no stage at 150 has been run.
* **250 concurrent: NOT yet verified** — no stage at 250 has been run.
* Blockers to a meaningful full run (all addressable via §4):
  1. `INTERNAL_SERVICE_SECRET` not set on backend/chat-edge → per-user keying
     inactive (shared-key 429 collision above 60 req/min).
  2. Mock provider not wired (`PROVIDER_BASE_URL_OVERRIDES` empty) → a full run
     would hit the **real** OpenAI key (cost).
  3. Dev backend is single-worker `--reload` — not representative of prod
     (resolved by FIX131: the stack runs the image's gunicorn workers).

Do **not** claim 150 or 250 is supported until those stages actually pass.
