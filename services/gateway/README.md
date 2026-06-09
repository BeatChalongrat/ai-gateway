# gateway — Phase 1 (Go strangler-fig entry point)

The Go gateway takes over the public `:8080` listener and sits in front
of the Python backend. It is a **transparent reverse proxy**: every
request — streaming and non-streaming — is forwarded to Python
unchanged. Python remains authoritative for routing, DLP, full auth,
provider dispatch, and request logging.

```
Before:  client ──> Python (:8080)
Phase 1: client ──> Go gateway (:8080) ──> Python (backend:8080)
```

## What the gateway does

- **Single public entry point** for all traffic (`/v1/*`, `/portal/*`,
  `/admin/*`, `/auth/*`, …) — catch-all proxy to Python.
- **Coarse API-key auth on `/v1/*` only.** SHA-256 hash → `api_keys`
  lookup (Redis-cached 30s), mirroring Python's `verify_key`
  (active + not expired). It is a strict *subset* of Python's check:
  it can reject early what Python would also reject, never admit what
  Python would deny. On any DB/Redis error it **fails open** (proxies;
  Python re-auths). JWTs (`eyJ…`) pass straight through.
- **Prometheus metrics** at `/__gw/metrics` (low-cardinality: bucketed
  path groups, no tenant labels).
- **Request-ID propagation** (`X-Request-Id`) into Python logs.
- **Liveness/readiness**: `/__gw/health`, `/__gw/ready`.
- **Graceful shutdown** (30s drain for in-flight streams).

## What the gateway deliberately does NOT do (Phase 1)

- ❌ Parse or modify SSE — streaming is byte-for-byte passthrough.
- ❌ DLP scan/restore, model routing, provider key decrypt, provider
  calls, cost/token accounting, fallback. All stay in Python.
- ❌ Hold any DLP/request state — there is none to hold; Python's
  single-request flow is untouched.

These are the Phase-2 candidates, once Go owns the full non-streaming
flow (routing + dispatch + formatting + cost + logging) and can call
the Rust DLP sidecar directly.

## Streaming correctness

`httputil.ReverseProxy` with `FlushInterval = -1` flushes every write
immediately, so provider token deltas reach the client in real time —
identical to when Python owned the socket. The proxy never buffers,
never sets a response-read timeout (streams can run minutes), and aborts
the upstream when the client disconnects (request-context cancellation).

## Ports (compose)

| Service | Container | Host publish | Notes |
|---------|-----------|--------------|-------|
| gateway | 8080      | **8080**     | new public entry point |
| backend (Python) | 8080 | 8081     | bind unchanged; host-published on 8081 for direct debugging. Gateway reaches it at `http://backend:8080`. |

Python's Dockerfile bind (`-b 0.0.0.0:8080`) and `/health` healthcheck
are **unchanged** — only the host port mapping moved.

## Config (env)

| Var | Default | Purpose |
|-----|---------|---------|
| `PORT` | `8080` | listener |
| `PYTHON_SERVICE_URL` | — (required) | upstream, e.g. `http://backend:8080` |
| `DATABASE_URL` | — (required) | api_keys lookup |
| `REDIS_URL` | — (required) | auth cache |
| `GATEWAY_AUTH_CACHE_TTL` | `30s` | valid-key cache window |
| `GATEWAY_MAX_BODY_BYTES` | `26214400` (25 MiB) | `/v1/*` body guard; 0 disables. Sized for base64 vision payloads. |
| `GATEWAY_PG_MAX_CONNS` | `10` | gateway PG pool |

## Build & test

```sh
cd services/gateway
go mod tidy      # generates go.sum (commit it for reproducible builds)
go vet ./...
go test ./...
```

No `INTERNAL_SERVICE_SECRET` is needed in Phase 1 — the gateway forwards
the original `Authorization` header and Python authenticates as it
always has.
