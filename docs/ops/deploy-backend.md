# Backend: how it runs, how to deploy it, how to roll it back (FIX131)

Until FIX131 the live backend was a development process: `uvicorn --reload` on
a bind mount of the checkout, one process, `APP_ENV=development`. Every saved
file went live; one process hit 130–182 % CPU with ten users chatting (NIGHT3:
event-loop lag 5 → 44 ms, first-token p95 15 s after 6 minutes, the same curve
on the mock provider). And no service had a `stop_grace_period`: on this
Docker Desktop the engine default is **1 second** (measured: a container that
traps SIGTERM exits 137 after 1.4 s), so every restart SIGKILLed the turns in
flight.

Since FIX131 live runs the **image**, like production.

## What runs

| | live (`docker compose -p ai-gateway up -d`) | dev hot reload (opt-in) |
|---|---|---|
| backend command | image CMD `backend/serve.sh`: `alembic upgrade head`, then `exec gunicorn -c gunicorn.conf.py` | `uvicorn --reload` |
| processes | gunicorn master (PID 1) + `GUNICORN_WORKERS` uvicorn workers | one |
| code | what the image was built from | the checkout, live on save |
| source mounts | none (only the `backend_uploads` volume at `/app/uploads`) | `./backend:/app` + read-only test mounts |
| ops-watch | the image's `app/ops/watch.py` | the checkout's (read-only) |
| files | `docker-compose.yml` + `docker-compose.override.yml` | + `docker-compose.dev.yml` |

**Editing the checkout no longer changes live.** A change reaches live only
when an image is built and deployed (below).

### Knobs (`.env` / compose; read by `backend/gunicorn.conf.py`)

| knob | default | why |
|---|---|---|
| `GUNICORN_WORKERS` | 4 | NIGHT3 saturated ONE process at ~1.3–1.8 cores; 4 workers give ~3× that and leave 8 of the VM's 12 vCPU to NER (2 workers, CPU-heavy), the two Postgres, chat-edge, the gateway, three `next dev` servers. Postgres budget: 35 connections per worker (sync 5+5, async 10+15) × 4 = 140 of `max_connections` 250, plus one leader-election connection per worker. 6 workers would be 210 + the others — raise the pool or `max_connections` first. Memory: ~200 MB per worker. |
| `GUNICORN_TIMEOUT` | 60 s | worker heartbeat. Uvicorn workers heartbeat from their event loop every timeout/2, **not per request**: a 15-minute stream never trips it; only an event loop blocked for 60 s does (then the worker is wedged and replacing it is right). The SIGTERM path does not apply it. It would cut a stream during a worker-level recycle (HUP, `max_requests`), so `max_requests` stays 0 and deploys never use HUP. |
| `GUNICORN_GRACEFUL_TIMEOUT` | 180 s | how long in-flight requests get after SIGTERM. Hidden thinking can run ~2 min before the answer streams; chat-edge keeps such a turn alive (10 s keepalive comments). |
| `stop_grace_period` (compose) | 200 s | how long Docker waits before SIGKILL: the drain + the lifespan shutdown (request-log flush, leader hand-over). Must stay above `GUNICORN_GRACEFUL_TIMEOUT`. |
| `GUNICORN_KEEPALIVE` | 5 s | what uvicorn used standalone. |

What SIGTERM does, measured with a harness app on the same config (a 30 s SSE
stream, SIGTERM at 5 s): the stream finished 30/30 frames with `[DONE]`; new
connections were refused from the first moment; `docker stop` returned after
26 s with exit 0. With nothing in flight `docker stop` takes ~1.5 s.

### The healthcheck while draining

After SIGTERM the listening socket is closed, so `/ready` cannot answer. With
the old healthcheck Docker marked the draining container **unhealthy within
11 s** (measured) — and autoheal restarts unhealthy containers, which would cut
the drain and race `docker compose up`'s recreate. `backend/healthcheck.py`
reports healthy while `/tmp/sran-backend/draining` exists (written by the
gunicorn master on SIGTERM, wiped at every start); otherwise it is the same
`/ready` probe. (An image built before FIX131 has no `healthcheck.py`; the
compose healthcheck falls back to the old inline probe.)

## One process, N workers: what runs where

Every worker runs `main.py`'s lifespan. Work that describes or serves the
worker itself runs in each; work that acts on the world runs once per
deployment, in the worker holding the **leader lock**.

| job | where | why |
|---|---|---|
| event-loop monitor, DB pool sampler | every worker | they measure that worker |
| pg_stat_activity sampler | every worker | feeds that worker's `/ready`; the same DB, so every worker sees a stuck transaction within `pg_activity_sample_seconds` (15 s) — 4 cheap queries per 15 s |
| request-log queue, custom-detector loader (Redis pub/sub), NER preload, provider-seed/admin-seed (advisory xact locks 900005/900008) | every worker | per-process queues/caches |
| generation job loop | every worker | each claim takes advisory lock 900004 |
| model-registry follower (new) | every worker | reloads that worker's registry when `model_configs` changes (md5 of the active rows, every 15 s): an admin edit reloads only the worker that handled it |
| worker-board publisher (new) | every worker | what `/ready` merges (below) |
| alert sweep | **leader** | N sweeps sent each alert up to N times (cooldown check and write are not atomic) |
| nightly retention pipeline | **leader** | the archive step copies then deletes; N runs archive rows N times |
| partition maintenance | **leader** | check-then-CREATE races |
| search-provider health probes | **leader** | each probe is a paid query |
| model discovery (APScheduler) + weekly capability probe | **leader** | N simultaneous `sync_all` runs |
| default alert-rule seed | every worker, serialized | advisory xact lock 900010: it is check-then-insert without a unique key |

**Leader election** (`backend/app/core/leader.py`): a session-level Postgres
advisory lock, id **900009**, on a dedicated autocommit connection
(`application_name = sran-backend-leader`, one per worker). Chosen over a Redis
lease because Postgres releases the lock the instant the holding session ends
(clean exit, crash, SIGKILL) — no TTL to tune, and a stalled leader cannot be
expired into a second leader while it still holds the session; and because it
spans containers, so the old and the new container of a deploy still elect one
leader. Followers retry every 5 s; the leader re-checks `pg_locks` every 5 s
and steps down if its session is gone. On shutdown the leader resigns first.
Measured on a scratch stack: SIGKILL of the leader worker → the server freed
the lock in 0.06 s, another worker held it 1.2 s later; all four workers'
`/ready` named the same leader. Needs a direct Postgres session — behind
PgBouncer transaction pooling a session lock means nothing.

### `/ready` and `/metrics` with N workers

`/ready` is answered by whichever worker gets the connection. The engine
checks (database, redis, DLP, loop lag) stay per worker. The container-wide
parts come from the **worker board**: each worker writes a small JSON record
every 5 s into `/tmp/sran-backend/workers/` (ignored after 15 s; removed when
the worker exits). So `stuck_requests` fails the probe when ANY worker has a
stuck request, `checks.turns` is the container's last turn, and
`checks.container` = `{workers, inflight, stuck, leader_pid}`.

`/metrics` is merged across workers (prometheus_client multiprocess mode,
`/tmp/sran-backend/prometheus/`, wiped at start; gunicorn's `child_exit` drops
a dead worker's live gauges). Counters and histograms are summed (a dead
worker's counts stay, so they never go backwards). Gauges: `livesum` for
per-worker quantities (`http_requests_in_flight`, `db_pool_*`,
`async_db_pool_*` — these are now **container totals**, e.g. `db_pool_max` 40
for 4 workers — `request_log_queue_size`, `credit_deduction_inflight`,
`gen_jobs_active`), `livemostrecent` for shared observations (`pg_*`,
`gen_queue_depth`, `replicate_*_spend_usd`). `/metrics.json` (legacy admin
view) is still per worker.

### State that is still per worker (known, bounded)

* 30 s TTL caches cleared only in the worker that handled an admin write:
  fallback policy / kill switch, resolved provider keys, provider base URLs,
  routing settings, keyword rules; AI-review settings 60 s. Other workers
  follow within the TTL.
* The Secure Trace buffer is in memory: an admin lookup finds a trace only if
  it lands on the worker that served the turn (~1 in N).
* Per-provider concurrency caps (`PROVIDER_CONCURRENCY_CAPS`, unset live) and
  the chat admission cap (64) are per worker: effective = cap × N.
* The Redis-outage "conservative" limiter is per worker (× N during an outage).
* In-process NER status (only used when `NER_SERVICE_URL` is empty — live
  uses the NER service).

### APP_ENV stays `development`

`production` also turns on Secure auth cookies, refuses the dev secret
defaults at boot, sends mail through Resend instead of the console, and
removes the mock search provider — each a behaviour change with its own test
pass, not part of the process model.

## Deploy

From the main checkout, after the change is merged and the gates are green:

```bash
scripts/deploy-backend.sh                 # build HEAD from git archive, deploy start-first
scripts/deploy-backend.sh --rev 1a2b3c4   # a specific commit
```

On battery the script says `REFUSED` and exits 3 before it builds an image or
touches a container (`scripts/ops/power.sh`; `docs/ops/monitoring.md`, "The
host's power"). `RUN_ON_BATTERY=yes` on the command runs it anyway.

What it does:

1. refuses a compose shape that runs `--reload` (the dev file);
2. builds `ai-gateway-backend:<sha>` from a clean `git archive`
   (`scripts/build-images.sh`), stamped with the revision label;
3. tags the image the running container uses as
   `ai-gateway-backend:rollback-<its revision>`;
4. refuses start-first when the new image would migrate the schema under the
   running version (`alembic heads` of the new image ≠ `alembic current`) —
   re-run with `--allow-migrations` if the migration is expand-only (old code
   keeps working on the new schema), else `--stop-first`;
5. opens the alert maintenance window for 10 minutes (`python -m app.ops.maintenance
   start 10 "backend deploy <rev>"` inside ops-watch; `DEPLOY_MAINTENANCE_CMD` overrides
   the command, `""` skips it) and closes it (`stop`) at the end, so the planned
   restart never pages at top priority;
6. tags the new image `ai-gateway-backend:latest` (what compose runs);
7. **start-first**: `docker compose run -d --no-deps --use-aliases --name
   ai-gateway-backend-bridge backend` — the new image on the compose network
   under the alias `backend`, no host ports — and waits until it is healthy;
8. `docker compose up -d --no-deps --force-recreate --timeout 200 --wait
   backend`: the old container drains (new connections to it are refused, so
   the gateway's dialer takes the bridge's address of `backend`), the new one
   starts and turns healthy;
9. drains and removes the bridge (`docker stop -t 200`, `docker rm`);
10. `docker compose up -d --no-deps ops-watch` (same image);
11. prints the probe summary and the rollback command.

Rehearsed on a scratch compose project (postgres, redis, backend, the Go
gateway in front): start-first — 0 of 39 probes through the gateway failed;
stop-first — the longest failure window was 6.2 s (idle drain + boot).

`--stop-first` skips the bridge: new requests fail while the old container
drains (up to the longest in-flight turn) and the new one boots (~10–15 s).

The manual equivalent, stop-first:

```bash
scripts/build-images.sh --rev <sha> backend                         # -> ai-gateway-backend:<sha>
docker tag "$(docker inspect -f '{{.Image}}' ai-gateway-backend-1)" ai-gateway-backend:rollback-<old sha>
docker tag ai-gateway-backend:<sha> ai-gateway-backend:latest
docker compose -p ai-gateway up -d --no-deps --force-recreate --timeout 200 --wait backend
docker compose -p ai-gateway up -d --no-deps ops-watch
```

### Measuring downtime

The script polls `--probe-url` (default `http://127.0.0.1:8080/live`: the Go
gateway → backend path) every 0.25 s and prints `N requests, F failed,
longest failure window S`. The same by hand, in a second shell:

```bash
while :; do printf '%s %s\n' "$(date +%s%3N)" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8080/live)"; sleep 0.25; done | tee probe.log
```

For in-flight turns, watch `docker logs -f ai-gateway-backend-1`: the master
logs `SIGTERM: draining in-flight requests for up to 180s`, workers log
`Waiting for connections to close` until their last stream ends.

## Roll back

```bash
scripts/deploy-backend.sh --image ai-gateway-backend:rollback-<rev>
```

An image built before FIX131 has the old CMD (`sh -c "alembic && gunicorn"`,
gunicorn not PID 1): it ignores SIGTERM, so its stop waits the full grace
period. Set `DEPLOY_STOP_GRACE=30` when replacing one.

## First deploy of FIX131 (one time)

1. **Avatars.** They lived in the bind-mounted checkout
   (`backend/uploads/avatars`); the live service now keeps `/app/uploads` in
   the named volume `ai-gateway_backend_uploads`. Create it with compose's
   labels and copy the files in BEFORE the deploy:

   ```bash
   docker volume create --label com.docker.compose.project=ai-gateway \
     --label com.docker.compose.volume=backend_uploads ai-gateway_backend_uploads
   MSYS_NO_PATHCONV=1 docker run --rm --network none -v ai-gateway_backend_uploads:/dst \
     -v "C:\Users\Beat Beat\Desktop\ai-gateway\backend\uploads:/src:ro" \
     ai-gateway-backend:<sha> sh -c 'cp -a /src/. /dst/ && ls -la /dst/avatars'
   ```

2. **What "previous version" means.** The container being replaced runs the
   checkout's code through the bind mount, not its image (the image is older).
   The `rollback-<rev>` tag the script makes of it is therefore NOT what was
   running. To go back to exactly the pre-FIX131 behaviour use the dev shape,
   which runs the checkout: `docker compose -p ai-gateway -f docker-compose.yml
   -f docker-compose.override.yml -f docker-compose.dev.yml up -d backend
   ops-watch`.
3. Expect ~+0.6 GB steady (4 workers of ~200 MB instead of one process; the
   file watcher that polled the bind mount goes away — the reload process
   idled at ~27 % of a core) and ~+1 GB during the ~1 minute overlap.
4. The test stack (`scripts/test-stack.sh`) runs the same image process
   (`docker-compose.test-stack.yml` has no command override since FIX131).

## Dev hot reload (opt-in)

```bash
docker compose -p ai-gateway -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.dev.yml up -d backend ops-watch      # checkout, --reload
docker compose -p ai-gateway up -d backend ops-watch     # back to the image
```

Never leave the live stack in the dev shape: every edit to the checkout
deploys itself (the FIX88/FIX90 history), and the deploy script refuses to run
against it.

## Tests

Backend tests never run in the live container. `scripts/backend-test-lane.sh`
runs them in a throwaway container (`--network none`, the checkout mounted
read-only, `APP_ENV=test`); `make test-unit` calls it.
