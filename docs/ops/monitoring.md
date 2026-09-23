# Monitoring and self-healing (FIX129)

What the 2026-09-22 incident (NIGHT2) showed: the backend wedged for 34
minutes on one abandoned `SELECT … FOR UPDATE` transaction. Every Postgres
timeout was 0, so the lock was held until the session died; the backend's
`/ready` healthcheck failed the whole time, but `restart: unless-stopped`
only reacts to a process exiting — a container that is "Up (unhealthy)" is
left alone; `alert_rules` was empty, so nobody was told. Three fixes, each
with its own evidence.

## Postgres server-level safety nets (A1)

Both servers (`postgres` in `docker-compose.yml`, `postgres-webchat` in
`docker-compose.webchat.yml`) now start with:

| Setting | Value | Effect |
|---|---|---|
| `idle_in_transaction_session_timeout` | `120000` (120 s) | a session that sits inside an open transaction doing nothing for 120 s is terminated and its locks are released — the last resort for clients that bypass the per-service limits below (psql, scripts); the services themselves heal at 30 s, and the "transaction stuck > 60 s" alert fires before the server kill |
| `lock_timeout` | `30000` (30 s) | a statement that waits more than 30 s for a lock fails with `55P03 lock_not_available` instead of queueing forever |
| `statement_timeout` | **not set** (0) | on purpose: `pg_dump`, `VACUUM`, migrations and the backup drill need long statements; the services set their own per-connection `statement_timeout` in code |

Check on a running server (read-only):

```sh
docker exec ai-gateway-postgres-1       psql -U postgres -d ai_gateway -At -c "show idle_in_transaction_session_timeout" -c "show lock_timeout"
docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai    -At -c "show idle_in_transaction_session_timeout" -c "show lock_timeout"
```

Expected `2min` / `30s`. `0` / `0` means the container was not recreated
after the compose change (`docker compose … up -d postgres` recreates it;
`restart` does NOT re-read the command). `docker-compose.prod.yml` overrides
only `ports` and `restart` for `postgres`, so the command survives the prod
overlay.

A terminated idle transaction shows in the server log as
`FATAL: terminating connection due to idle-in-transaction timeout`; the
client sees its next statement fail and must reconnect (pool clients do).

### Per-service limits (set on every connection, in code)

The server net above is the last resort. Each service sets its own limits on
every connection it opens, and names itself in `pg_stat_activity`
(`application_name`), so the next incident report can say who held the lock:

| Service | `statement_timeout` | `lock_timeout` | `idle_in_transaction_session_timeout` | `application_name` | Where |
|---|---|---|---|---|---|
| backend (sync + async engine) | 60 s | 10 s | 30 s | `sran-backend`, `sran-backend-async` | `backend/app/core/db.py` (`DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`, `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`) |
| backend — the credit deduction only | (inherits) | **5 s** | **15 s** | | `SET LOCAL` in `portal_credit_check.deduct_portal_credit_async` (`credit_deduction_lock_timeout_ms`, `credit_deduction_idle_timeout_ms`) |
| chat-edge (pgxpool) | 30 s | 5 s | 30 s | `chat-edge` | `services/chat-edge-api/internal/db/pool.go` (`PG_STATEMENT_TIMEOUT_MS`, `PG_LOCK_TIMEOUT_MS`, `PG_IDLE_IN_TRANSACTION_TIMEOUT_MS`) |
| ops-watch (its pg_stat_activity read) | 5 s | 2 s | — | `ops-watch` | `backend/app/ops/watch.py` |

A backend statement that legitimately needs more than 60 s (a long
migration, a big archive step) must `SET LOCAL statement_timeout = 0` for
itself; the schema guard's `alembic upgrade` runs on the same engine, so a
future long-running migration would hit the 60 s wall unless it does.

### The credit deduction is instrumented (A2)

The transaction that wedged the live system (`SELECT … FROM portal_users …
FOR UPDATE` in `deduct_portal_credit_async`) could not be reproduced, so it
is observed rather than "fixed":

* one log line per deduction — `credit_deduction request_id=… user_id=…
  outcome=ok|lock_timeout|cancelled|error credits=… elapsed_ms=…
  lock_wait_ms=… slow=…` — INFO normally, WARNING when slow
  (`credit_deduction_slow_seconds`, 2 s) or not ok;
* `/metrics`: `credit_deduction_inflight` (stays > 0 when one is stuck),
  `credit_deduction_seconds`, `credit_deduction_lock_wait_seconds`,
  `credit_deduction_total{outcome}`;
* the pg_stat_activity sampler (below) names the pid / application_name /
  query of anything idle in transaction or blocked on a lock.

## The pg_stat_activity sampler and `/ready` (A2/A3)

`backend/app/services/pg_activity.py` reads `pg_stat_activity` every
`pg_activity_sample_seconds` (15 s) for sessions idle in transaction or
waiting on a lock, publishes `pg_idle_in_transaction_sessions`,
`pg_idle_in_transaction_max_age_seconds`, `pg_lock_waiters`,
`pg_stuck_transaction_age_seconds`, and logs `pg_stuck_transaction` with the
offending sessions once the oldest passes `pg_stuck_transaction_seconds`
(60 s). `/ready` reads the last sample: `checks.pg_activity`, and
`failures: ["stuck_transaction"]` (503) at the same threshold — so the
container healthcheck goes red and autoheal restarts the backend, which
closes its connections and releases the lock. `/ready` also reports
`checks.turns.last_success_age_s` / `last_attempt_age_s` (this worker).

## Alerts (A4)

### Channels

Configured in `.env` (never committed), read by the backend, the ops
watcher and the backup job:

| Variable | Channel | Notes |
|---|---|---|
| `ALERT_NTFY_URL` | ntfy push (phone) | a full topic URL, e.g. `https://ntfy.sh/<random topic>`; the owner subscribes to it in the ntfy app. `ALERT_NTFY_TOKEN` for a protected topic. |
| `ALERT_EMAIL_TO` | e-mail via Resend | needs `RESEND_API_KEY`; in `APP_ENV=development` the sender only logs |
| `ALERT_WEBHOOK_URL` | JSON POST | the same payload the alert rules' own `webhook_url` gets |

A notification carries a title, a body, a priority (critical = 5, warning =
4, info = 3) and tags; it never carries a secret. Example (ntfy):

```
Title:    No successful turn in 5 min (with attempts): 12 request(s) in the last 5 min, none succeeded
Priority: 5
Tags:     rotating_light,critical,no_successful_turn
Body:     No successful turn in 5 min (with attempts): 12 request(s) in the last 5 min, none succeeded
          window_minutes: 5
          metric_value: 12.0
          threshold: 1.0
```

### Rules (backend, `alert_rules`, evaluated every `alert_sweep_interval_seconds` = 10 s)

Seeded at startup when no rule with the metric exists (`alert_defaults.py`;
an operator's own rule with the same metric is never overridden):

| Rule | Metric | Fires when | Cooldown |
|---|---|---|---|
| No successful turn in 5 min (with attempts) | `no_successful_turn` | ≥ 1 request in the window and 0 with `status=success` — a quiet night is not an outage. Gateway-level: only requests that reached a provider are in `request_logs` (see the ops watcher's turn rules for the chat-level truth) | 5 min |
| Error rate above 5 % (5 min, ≥10 requests) | `error_rate` | ≥ 10 requests in the window and > 5 % have a status other than `success` / `blocked` (same gateway-level caveat) | 5 min |
| Transaction stuck longer than 60 s | `stuck_transaction` | the sampler's oldest idle / lock-blocked transaction is ≥ 60 s | 1 min |

Every fired rule is written to `alert_events` (`delivery_status`
delivered / failed / log_only) and sent on every configured channel.

### The ops watcher (`ops-watch`, `backend/app/ops/watch.py`)

The alerts the backend cannot raise about itself, every `OPS_WATCH_INTERVAL`
(30 s), announced once per `OPS_WATCH_COOLDOWN` (10 min) and followed by a
"recovered" note:

| Finding | Source | Threshold |
|---|---|---|
| `<service> is unhealthy` / `is restarting` / `has exited` | Docker API (read-only socket), containers of the compose project | any |
| `backend is not ready` / `does not answer /ready` | `GET http://backend:8080/ready` from outside | 503 or no answer in 6 s |
| `disk /host/docker is N % full` | `statvfs` of the Docker data root | `OPS_WATCH_DISK_PCT` (90) |
| `memory is N % used` | `/proc/meminfo` of the Docker VM | `OPS_WATCH_MEM_PCT` (90) |
| `transaction stuck N s on gateway|chat` | `pg_stat_activity` on both databases (`OPS_WATCH_PG_DSNS`) | `OPS_WATCH_PG_STUCK_S` (60) |
| `no successful turn in 5 min` / `N % of turns failed (5 min)` | `chat.messages_masked` assistant rows `completed` / `error` on the chat database (the `chat` DSN) | ≥ 1 turn and 0 completed; ≥ `OPS_WATCH_TURN_MIN_SAMPLE` (10) turns and more than `OPS_WATCH_TURN_ERROR_PCT` (5) % failed |

Why the turn rules live here as well as in `alert_rules`: the gateway's
`request_logs` only records requests that reached a provider. On the test
stack, 12 turns with every model dead produced 12 failed assistant rows in
the chat database and **0** `request_logs` rows — so the backend's
`no_successful_turn` / `error_rate` rules would have stayed silent. The chat
database is where a turn's outcome is truthfully recorded (web and desktop),
and the ops watcher reads it from outside the backend.

It runs from the backend image with the docker socket read-only; it is not
part of the test stack. `python -m app.ops.watch once` runs a single tick
(the test-fire recipe in `scripts/probes/FIX129/`).

## Sign-out ends the session (B2)

`POST /portal/auth/logout` revokes the refresh token (as before) AND the
access token's `jti` (`portal_access_revoked:<jti>` in the Portal's Redis,
checked by `get_portal_user`), then calls chat-edge's
`POST /internal/auth/revoke` (gated by `INTERNAL_SERVICE_SECRET`,
`CHAT_EDGE_INTERNAL_URL`), which keeps its own list (`auth:revoked:jti:*`,
`auth:revoked:user:*` in its Redis) and answers 401 from the next request.
The web client polls `GET /api/v1/auth/session` every 5 s while the tab is
visible, so a signed-out or copied token is off every open tab within
seconds. Both revocation checks fail OPEN on a Redis outage (availability
first, logged as `access_token_revocation_check_failed` /
`auth_revocation_check_failed`). A deleted chat's files answer 404 on
`/files/{id}/download`, `/text` and `/versions` while the chat is deleted
(B1, `file_repo.go`).

## autoheal: a watchdog that acts on "unhealthy" (A3)

`autoheal` (`willfarrell/autoheal:1.2.0`, in `docker-compose.yml`) polls the
Docker daemon every 10 s and restarts any container labelled `autoheal=true`
whose health is `unhealthy` (graceful stop, 20 s; 60 s grace after autoheal
itself starts). Labelled: `backend`, `gateway` (base file), `chat-edge-api`,
`web-chat` (webchat file) — the services that have a healthcheck. With the
backend's healthcheck (`/ready`, 10 s interval, 3 retries) the wedge above
would have ended in about 40 s instead of 34 minutes (and the stuck-transaction check in /ready makes the wedge itself a red healthcheck).

It needs the docker socket (= host control). It is therefore NOT part of the
test stack: `scripts/test_stack_gen.py` allowlists services and refuses any
socket mount.

See it working:

```sh
docker logs ai-gateway-autoheal-1          # "… (unhealthy) found, restarting …" lines
docker ps --filter label=autoheal=true     # who is watched
```

A restart by autoheal looks, from the service's side, like any restart:
`docker inspect <c> --format '{{.RestartCount}} {{.State.StartedAt}}'`
moves. If a container is restarted repeatedly the healthcheck is telling you
something the restart does not fix — read the container log, not autoheal's.

## Backups and the restore drill (A5)

`docs/ops/backups.md`: daily 03:15 Asia/Bangkok, both databases + uploads,
every backup restored into a scratch database and counted, alerts on
failure via ntfy / Resend.

## Log secret scan

`scripts/probes/FIX129/log_secret_scan.py` fails if any secret value from a
`.env` appears in a container log:

```sh
python scripts/probes/FIX129/log_secret_scan.py --env .env                 # whole history of every ai-gateway container
python scripts/probes/FIX129/log_secret_scan.py --env .env --since 24h     # since the last day
python scripts/probes/FIX129/log_secret_scan.py --env .env --project sran-test
```

* Secrets = the values of every key whose NAME matches
  `(PASSWORD|SECRET|KEY|TOKEN|DSN)`, plus the password part of ANY value that
  is a URL with `user:pass@` or a libpq DSN with `password=…`. Values shorter
  than 8 characters and the well-known dev defaults (`postgres`, `changeme`,
  `…change-in-prod…`) are ignored.
* It runs `docker logs` for every container whose
  `com.docker.compose.project` label is the project (running or stopped) and
  reports, per container, the KEY NAMES found and the counts — never a value,
  never the line.
* Exit 1 on any hit, 0 when clean, 2 on a usage error. Read-only.

The 2026-09-23 run against the live project
(`scripts/probes/FIX129/results/ops/log_secret_scan_live.txt`) was the
evidence for finding B3:

```
  RED   ai-gateway-rate-limiter-1: REDIS_PASSWORD x2 (first at log line 1)
  ok    (the other 18 containers)
RESULT: RED - 2 secret occurrence(s) in container logs
```

The rate-limiter's boot line logged `REDIS_URL` whole
(`redis://:<password>@redis:6379`). Fixed in `rate-limiter/src/main.rs`
(`redact_url`: `redis://:***@redis:6379`, unit-tested); the scan goes green
for that container once the rebuilt image is deployed — the old log lines
stay in the container's log until it is recreated, so expect the scan to
stay RED on `--since` windows that include the old boot until then.

The other services were checked for the same pattern:

| Service | Boot log | Result |
|---|---|---|
| `log-worker` | `log-worker/main.go:56-57` logs the DSN through `maskDSN` (`log-worker/main.go:191`), which overwrites the `password=` value with `*` | masked (B5) |
| `dlp-sidecar` | `dlp-sidecar/src/main.rs:209-214` logs ports and detector count only | nothing to leak |
| `gateway` (Go) | `services/gateway/cmd/server/main.go` parses `DATABASE_URL` and logs only `err` on failure | nothing logged |
| `chat-edge-api` | no boot line prints `DATABASE_URL` / `REDIS_URL`; the scan found 0 hits in 1.8 MB of log | clean |

Run the scan after every deploy that touches a boot log, and before sharing a
`docker logs` capture with anyone.
