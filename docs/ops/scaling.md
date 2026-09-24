# Scaling: from one machine to two (FIX133)

This page is the path from the single box we run today to a second one. It
scales on **queue depth and concurrency**, the way LLM serving is scaled,
not on CPU. Every claim below is checked against the code; file paths are
relative to the repository root. The turn queue and the Caddy proxy are the
FIX133 design (in progress on another branch) and are described as such.

`docs/SCALING_ACTIVATION_RUNBOOK.md` is the older "scaling" compose profile
(PgBouncer, a separate job worker, nginx). Its step 4 (move the singleton
jobs out of the web process) is superseded by the FIX131 leader lock below;
its PgBouncer notes still apply when Postgres connections run out.

## 1. What the box is today

```
browser (apps/web-chat, :3002)          desktop app (apps/desktop)
        |  /api/v1                              |  /v1 (gateway-client.ts)
        v                                       |
chat-edge-api (Go, :4000) --service JWT-->      |      API-key clients
        |                                       v          |
        +----------------------> gateway (Go, :8080, gw_ key auth, Redis auth cache)
                                        |  PYTHON_SERVICE_URL=http://backend:8080
                                        v
                        backend (gunicorn x4 uvicorn workers, host 127.0.0.1:8081)
                        | NER_SERVICE_URL   | DLP_GRPC_ADDRESS | rate-limiter | providers
                        v                   v                  v              v
                  ner-service:8001    dlp-sidecar:50051   rate-limiter    deepseek / openrouter /
                  (4 workers, 7 GB)   (Rust, DLP_ENGINE)  (Rust, 3 scopes) sranai (self-hosted,
                                                                            its own machine)
chat-edge-api --KB_MODELS_URL--> kb-models:8090 (CPU embeddings, 6 CPU / 6 GB)

state:  postgres:5432 ai_gateway  |  postgres-webchat (host :5433) sran_ai
        redis:6379 db0            |  redis-webchat (host :6380) db1
        volumes chat_edge_uploads, backend_uploads
sidecars: log-worker (Go), ops-watch, autoheal, backup, prometheus, grafana
```

Where each line comes from:

- Ports and images: `docker-compose.yml` (gateway l.320-372, backend l.606-875,
  ner-service l.154-206, dlp-sidecar l.207-237, rate-limiter l.238-279) and
  `docker-compose.webchat.yml` (chat-edge-api l.27-292, kb-models l.294-316,
  postgres-webchat l.318, redis-webchat l.354, web-chat l.434, backup l.504).
  The base compose also carries a chat-edge-api and kb-models behind
  `profiles: ["desktop"]` (l.465-573) that use `postgres:5432/sran_chat` and
  `redis:6379/2`; the web deployment uses the webchat file's pair.
- The browser talks to chat-edge (`NEXT_PUBLIC_API_URL`, webchat l.439) and
  chat-edge calls the gateway with a 60 s HS256 service JWT
  (`services/chat-edge-api/internal/auth/service_jwt.go`,
  `backend/app/api/deps.py` l.184-256). The desktop app posts
  `/v1/chat/completions` itself (`apps/desktop/src/main/gateway-client.ts`).
- The gateway keeps its API-key cache in Redis (`gwauth:` keys,
  `services/gateway/internal/auth/apikey.go` l.40-55), so it holds no state.
- The backend runs `GUNICORN_WORKERS` (default 4) `UvicornWorker` processes
  (`backend/gunicorn.conf.py` l.77-80). Each worker admits at most
  `chat_inflight_limit` = 64 chat turns (`backend/app/core/config.py` l.324,
  `backend/app/core/inflight.py`).
- The inbound DLP scan runs over the whole `payload.messages` array on every
  turn (`backend/app/api/routes/public_chat.py` l.640-642, "full-message
  inbound scan"), and the NER detectors call `ner_service_url`
  (`backend/app/core/config.py` l.521, `backend/app/services/ner_service_client.py`).
  That is why NER cost grows with conversation length.

### What is already safe to run twice

- Gateway: stateless (auth cache in Redis).
- Backend singleton jobs (alert sweep, retention pipeline, partition
  maintenance, search health, model discovery) run only on the worker that
  holds Postgres advisory lock 900009 (`backend/app/core/leader.py` l.12-13,
  `backend/app/main.py` l.250-266). A second backend box elects one leader
  between all its workers the same way.
- Backend rate limits, response cache, idempotency and gateway auth are in
  Redis: `rl:key:` / `rl:proj:` / `rl:tenant:`
  (`backend/app/services/rate_limit_service.py` l.265-285; the Rust limiter's
  Lua script in `rate-limiter/src/sliding_window.rs` l.37-56 uses the same
  three scopes), `cache:` (`backend/app/services/cache_service.py` l.88),
  `idem:` (`backend/app/middleware/idempotency.py` l.114), `gwauth:`.
- chat-edge in Redis: `ratelimit:` (`internal/middleware/ratelimit.go` l.52),
  `auth:revoked:jti:` / `auth:revoked:user:` (`internal/auth/revocation.go`),
  `chat:runlease:` (`internal/service/run_lease.go`), `chat:runledger:`
  (`internal/service/stream_ledger_store.go`, TTL `STREAM_LEDGER_TTL_SECONDS`
  = 600), `chat-edge:memextract:*` and `chat-edge:planexec:*`.
- chat-edge knowledge indexing claims documents with `FOR UPDATE SKIP LOCKED`
  (`internal/repository/knowledge_index_repo.go` l.50); the cloud-task
  scheduler leases tasks in the DB (`internal/service/cloud_task_runner.go`
  l.58-73); the interrupted-turn sweep only settles rows whose lease is gone
  ("here or on another instance", `cmd/server/main.go` l.253-256).

## 2. The signals to scale on

Scale when turns **wait**, not when CPU is busy. A turn that is streaming
from a provider uses almost no CPU here; a box at 30 % CPU can still be
queueing users because the admission cap is full.

| Signal | Where it comes from | Meaning |
|---|---|---|
| `checks.turn_queue.waiting` | FIX133 design: `GET /ready` on the backend gains `checks.turn_queue` = `{active, waiting, max_active, oldest_wait_s}` from the Redis ZSETs `sran:turnq:active` / `sran:turnq:wait` | turns admitted vs turns holding a `queued` SSE frame |
| `checks.turn_queue.oldest_wait_s` | same | how long the longest-waiting person has been looking at "position N" |
| `503 queue_full` count | backend logs / request_logs `error_code` once the queue lands | the wait line (`TURN_QUEUE_MAX_WAITING` = 4 x cap) overflowed |
| `checks.inflight` | already in `/ready` today (`backend/app/api/routes/health.py` l.266-272: count, oldest_age_s, stuck, limit, available) | per-worker admissions against `chat_inflight_limit` |
| p95 turn duration | `http_request_duration_seconds` (`backend/app/middleware/metrics.py` l.23), `gateway_request_duration_seconds` (`services/gateway/internal/metrics/metrics.go` l.26), `request_logs.latency_ms` | whole-turn latency; there is **no time-to-first-token metric today** (nothing named first_token/ttft in backend, gateway or chat-edge; chat-edge exposes only `/health`, `internal/router/router.go` l.141). Add a TTFT histogram alongside the queue; until then use the queue's own `estimate` |

Rules of thumb for one box, cap = CPU count (`TURN_QUEUE_MAX_ACTIVE` default):

1. `waiting > 0` for 5 consecutive minutes on a normal weekday -> plan the
   second box (warning, not a page).
2. `oldest_wait_s > 30` -> people are noticing; page if it holds for 5 min.
3. Any `queue_full` in a 15-minute window -> capacity incident; the wait
   line is 4 x cap deep, so this means sustained overload, not a burst.
4. p95 turn duration rising while `waiting == 0` -> the bottleneck is a
   sidecar (NER, kb-models) or the provider, not admission. Do not add a
   backend box for this; see step 1 below.

Who watches today: `ops-watch` (`backend/app/ops/watch.py`) probes `/ready`
but only classifies up / down / stuck-transaction (`classify_ready` l.225-241);
its turn rules read the chat database (`classify_turns`, `classify_stuck_turns`
l.183-214) with `OPS_WATCH_TURN_*` thresholds and notify through
`ALERT_NTFY_URL` / `ALERT_WEBHOOK_URL` / `ALERT_EMAIL_TO`. The design step
that completes FIX133 is a `turn_queue` finding in ops-watch that reads the
new `checks.turn_queue` block with two thresholds (`OPS_WATCH_QUEUE_WAIT_MIN`,
`OPS_WATCH_QUEUE_OLDEST_S`) and the same cooldown as the other findings.
Prometheus and Grafana are in the base compose (l.945-974) for the p95 curves.

## 3. Step 1: move the model-side sidecars to a second box

NER and kb-models are CPU-bound and stateless. They are the first thing to
leave, and it is a configuration change only.

What moves:

| Service | Who calls it | Setting to repoint | Size today |
|---|---|---|---|
| ner-service | backend (DLP detectors, every turn) | `NER_SERVICE_URL` (default `http://ner-service:8001`, `docker-compose.yml` l.709; `NER_SERVICE_TIMEOUT_SECONDS` l.710) | `NER_WORKERS`=4, limits 4 CPU / 7 GB (l.163-183) |
| kb-models | chat-edge (KB indexing and search) | `KB_MODELS_URL` (default `http://kb-models:8090`, `docker-compose.webchat.yml` l.160; also base l.501) | `KB_THREADS`=6, limits 6 CPU / 6 GB (l.547-573) |
| dlp-sidecar (optional) | backend, only when `DLP_ENGINE=rust` (default `python`, l.791) | `DLP_GRPC_ADDRESS` (default `dlp-sidecar:50051`, l.792) | 2 CPU / 512 MB |

The self-hosted models already live elsewhere: the `sranai` provider is a
base URL per key (for example `https://gateway.9arm.co/v1`), so nothing on
this box serves tokens.

How:

1. On box B run only `ner-service` (and `kb-models`) from the same images,
   listening on a private interface. Both have a `/health` (compose l.185)
   and hold no state; kb-models needs its model volume (`kb_models`) copied
   once or `KB_MODELS_OFFLINE=0` for the first pull.
2. On box A set `NER_SERVICE_URL=http://<box-B>:8001` and
   `KB_MODELS_URL=http://<box-B>:8090` in `.env`, then restart backend and
   chat-edge-api. `NER_SERVICE_TIMEOUT_SECONDS` (2.0) is the failure bound:
   the DLP call is fail-open past it, so a slow link degrades masking. Keep
   the two boxes on one switch and measure the round trip before cutting over.
3. Stop the local sidecars. The freed 10 CPU / 13 GB is what the turn cap
   `TURN_QUEUE_MAX_ACTIVE` = CPU count was competing with.

Why this first: FIX131 measured p95 climbing after 6-9 minutes at 10 non-stop
users because the full-history NER scan grows with every turn. Adding a
backend box would double the NER load instead of removing it.

## 4. Step 2: a second backend / chat-edge box

### 4.1 What must become stateless first (chat-edge)

These live in one process's memory today (`services/chat-edge-api`):

| State | File | What breaks with two replicas | Fix |
|---|---|---|---|
| `turns sync.Map` (one turn per conversation) | `internal/service/conversation_turns.go` l.15-17: "Single-replica assumption... a second replica would need the guard in Redis or Postgres" | two sends for one chat land on two boxes; both run; two assistant rows | move the claim to Redis `SET NX` keyed by conversation, same TTL as the run lease (`RUN_LEASE_TTL` 45 s, `internal/config/config.go` l.301) |
| `activeStreams sync.Map` | `internal/service/message_service.go` l.195; `CancelStream` does `LoadAndDelete` l.1884 | Stop lands on the box that is not streaming and returns false | publish cancel on Redis pub/sub keyed by conversation; each box cancels what it owns |
| `StreamBroker` fan-out | `internal/service/stream_broker.go` l.8-15: "IN-PROCESS ONLY... cross-instance resume then needs a shared broker (e.g. Redis pub/sub)" | replay of the recorded frames works from `chat:runledger:*` (600 s), but attaching to the **live tail** on the other box falls back to the DB-terminal path (the page waits for the row to finish) | Redis pub/sub per message id, or sticky routing by conversation id at the proxy (see 4.2) |
| `KnowledgeWorker.active` map | `internal/service/knowledge_worker.go` l.95 | none: claims are `SKIP LOCKED`; the map only cancels this box's own jobs | keep |
| Retention enforcer, transcript retention, export sweep, unattached-file sweep | `cmd/server/main.go` l.836-876 | both boxes run them on the same schedule; correct but doubled work and doubled log noise | one advisory lock or a Redis `SET NX` "leader" like the backend's `leader.py` |
| Cloud scheduler | `main.go` l.889+, DB lease (`cloud_task_runner.go` l.58-73) | none | keep |
| Local uploads `STORAGE_BACKEND=local` | `internal/config/config.go` l.349-362; volume `chat_edge_uploads` | a file uploaded on box A is missing on box B | `STORAGE_BACKEND=s3` with `S3_BUCKET`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_FORCE_PATH_STYLE` (implemented in `internal/storage/s3.go`; `docker-compose.webchat.yml` l.382-425 ships a MinIO under `profiles: ["minio"]`). Presigned URLs embed `S3_ENDPOINT`, so it must be a name the browser can resolve, not the compose-internal `minio:9000` |

Backend state that multiplies with replicas (fine, but know it): the
`InflightRegistry` is per worker (`backend/app/core/inflight.py`), the
provider semaphores are per worker process (`backend/app/services/providers/concurrency.py`
l.54-57, `PROVIDER_CONCURRENCY_CAPS`), and there are in-process TTL caches
(catalogue, settings). Two boxes therefore hold twice the provider
concurrency; if a provider has a hard concurrent limit, halve the caps.
Backend avatars are written to `backend_uploads:/app/uploads`
(`docker-compose.yml` l.831, `backend/app/main.py` l.937) and would need the
same S3 treatment or a shared mount. The FIX133 turn queue is in Redis, so it
counts turns across both boxes as one pool.

### 4.2 The proxy change

FIX133 puts Caddy in front: TLS, least-connections, active health checks and
graceful drain, with `3000/3001/3002/4000/8080` moving from loopback to the
same numbers over HTTPS. Adding a box is one upstream per service:

```
# design; the real Caddyfile lands with FIX133
:4000 {  reverse_proxy box-a:4000 box-b:4000 { lb_policy least_conn  health_uri /health  health_interval 5s } }
:8080 {  reverse_proxy box-a:8080 box-b:8080 { lb_policy least_conn  health_uri /__gw/health } }
```

Health paths that exist today: chat-edge `/health` (router l.141), gateway
`/__gw/health` (compose l.349), backend `/ready` (l.865). Until the
StreamBroker is shared, add sticky routing for `/api/v1/conversations/*`
(hash on the conversation id in the path) so a resume reaches the box that
owns the stream. The web-chat image inlines `NEXT_PUBLIC_API_URL`,
`NEXT_PUBLIC_GATEWAY_URL` and `NEXT_PUBLIC_PORTAL_URL` at build time
(`docker-compose.webchat.yml` l.427-462): switching to `https://` URLs means
rebuilding it, not just editing `.env`.

### 4.3 What stays single

- Both Postgres servers (`ai_gateway`, `sran_ai`) and both Redis servers stay
  on one box; the second box points at them over the private network. When
  connections run out, PgBouncer from the "scaling" profile applies, with
  the caveat that leader and worker locks are session-level and must bypass it
  (`docker-compose.yml` l.1012-1030).
- The `backup` container reads `chat_edge_uploads:ro` and dumps both
  databases (`docker-compose.webchat.yml` l.504-566); after the S3 move it
  backs up the bucket instead of the volume.
- `ops-watch`, `autoheal`, `log-worker`, `prometheus`, `grafana`: one copy
  each, on the box that holds the databases.

## 5. What not to do

- **Do not scale on CPU.** The turn cap is what refuses users; CPU at 40 %
  with `waiting > 0` is the normal shape of an overloaded LLM front end.
- **Do not add a second chat-edge before section 4.1.** Two `turns` maps
  means duplicated assistant rows; the code comments say so.
- **Do not run two copies of the singleton jobs.** The backend's are guarded
  by lock 900009; chat-edge's sweeps are not, and neither is the older
  `worker` container (`backend/worker.py`, "scaling" profile), which must not
  run alongside the FIX131 leader.
- **Do not raise `chat_inflight_limit`, `NER_WORKERS` or the queue cap to
  make a load test pass.** The cap equals CPU count for a reason; more
  admitted turns on the same CPUs raise p95 for everyone.
- **Do not put the second box on the public port numbers directly.** All
  ports are loopback today (`127.0.0.1:` in every compose entry); the proxy
  is the only listener.

## 6. Checklist

Before step 1:
- [ ] `checks.turn_queue` (or, until it lands, `checks.inflight`) recorded for two weeks; `waiting > 0` minutes per day known.
- [ ] Round-trip time A <-> B measured; below 1 ms on the same switch.

Step 1 (sidecars):
- [ ] ner-service and kb-models up on box B, `/health` green, model volume present.
- [ ] `NER_SERVICE_URL`, `KB_MODELS_URL` (and `DLP_GRPC_ADDRESS` if `DLP_ENGINE=rust`) set on box A; backend and chat-edge restarted.
- [ ] DLP masking verified on a Thai and an English prompt after the cutover (the call is fail-open on timeout).
- [ ] Local sidecars stopped; `TURN_QUEUE_MAX_ACTIVE` reviewed against the freed CPUs.

Step 2 (second backend/chat-edge):
- [ ] `STORAGE_BACKEND=s3` on both boxes; existing `chat_edge_uploads` files migrated; downloads verified from a browser.
- [ ] Turn claim, Stop and StreamBroker moved to Redis (or sticky routing in place); chat-edge sweeps behind one lock.
- [ ] Caddy upstream lists carry both boxes; health checks green; one box drained and re-added without a failed turn.
- [ ] Provider caps halved if the provider limits concurrency.
- [ ] ops-watch `turn_queue` finding and the backup job point at the shared bucket.
