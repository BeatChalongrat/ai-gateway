# Scaling Infra — Activation Runbook

PgBouncer, a singleton job **worker**, and **nginx** ship as **inert** services
behind the compose `scaling` profile. They do **nothing** under
`docker compose up -d` — the current single-pod stack is unchanged.

```bash
# bring them up additively (alongside the normal stack):
docker compose --profile scaling up -d

# verify they're absent by default:
docker compose config --services            # no pgbouncer/worker/nginx
docker compose --profile scaling config --services   # includes them
```

Bringing them up is **not** the same as wiring them in. By default:
- **pgbouncer** runs on `:6432` but **nothing points at it** (the backend still
  talks to `postgres:5432`).
- **worker** runs the singleton jobs **in addition to** the web lifespan jobs
  (harmless — the jobs are idempotent — but redundant until you remove the
  lifespan copies, step 4).
- **nginx** runs on `:80` and forwards to the gateway (additive; the gateway is
  still also on `:8080`).

The steps below are the real activation. Do them when you actually run multiple
backend pods. **Each is reversible.**

---

## 1. Repoint the backend at PgBouncer

The backend builds its DSN from `POSTGRES_HOST`/`POSTGRES_PORT`
(`config.database_url`), **not** a `DATABASE_URL` env var. So repointing is just
two env values on the `backend` service:

```yaml
# docker-compose.yml (or an override), backend.environment:
POSTGRES_HOST: pgbouncer
POSTGRES_PORT: 6432
```

Reduce the per-pod pool (PgBouncer now multiplexes). In an override or via env:

```
DB_SYNC_POOL_SIZE=2  DB_SYNC_MAX_OVERFLOW=2
DB_ASYNC_POOL_SIZE=3 DB_ASYNC_MAX_OVERFLOW=3
# -> 10 client conns/worker x 4 workers = 40, multiplexed into 25 PG conns.
```

The Go **gateway** can also repoint (`DATABASE_URL: …@pgbouncer:6432/…`), but it
only does the api_keys lookup, so it's optional.

The **worker stays on `postgres:5432`** (already set in its service env) — do
NOT point it at PgBouncer (step 3 explains why).

---

## 2. Disable server-side prepared statements (REQUIRED for transaction pooling)

The driver is **psycopg3** (`postgresql+psycopg://`, both engines). Under
PgBouncer transaction pooling a named prepared statement created on one server
connection won't exist on the next, so it must be disabled. For psycopg3 the
knob is **`prepare_threshold=None`** (NOT asyncpg's `statement_cache_size=0`,
and NOT `0` — `0` means *prepare on first use*).

In `backend/app/core/db.py`, add `prepare_threshold: None` to `connect_args` for
**both** the sync and async engines, e.g.:

```python
# sync engine connect_args:
_connect_args = {"client_encoding": "utf8"}
if _is_postgres and os.environ.get("DB_PGBOUNCER_MODE") == "1":
    _connect_args["prepare_threshold"] = None
# async engine kwargs (the postgresql branch):
if os.environ.get("DB_PGBOUNCER_MODE") == "1":
    kwargs.setdefault("connect_args", {})["prepare_threshold"] = None
```

Gate it on an env flag (`DB_PGBOUNCER_MODE=1`) so the direct-Postgres path is
unaffected. (`pgbouncer.ini` already sets
`ignore_startup_parameters = extra_float_digits,options` for the other psycopg
startup params.)

---

## 3. Migrations must BYPASS PgBouncer (DDL needs session mode)

The backend Dockerfile runs `alembic upgrade head && gunicorn …` at boot, and
alembic uses `settings.database_url` — which, after step 1, points at PgBouncer.
**DDL deadlocks under transaction pooling.** Run the alembic step against
Postgres directly via `DATABASE_URL_OVERRIDE` (pydantic-settings reads it and it
takes precedence over the assembled DSN):

```dockerfile
# backend/Dockerfile CMD — split so only migrations bypass PgBouncer:
CMD ["sh","-c","DATABASE_URL_OVERRIDE=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB} python -m alembic upgrade head && gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8080 --timeout 60 --graceful-timeout 30 --worker-connections 100"]
```

Runtime gunicorn (no override) then uses `POSTGRES_HOST=pgbouncer`. The same
applies to chat-edge SQL migrations — point them at `postgres:5432`.

**Why the worker bypasses PgBouncer (step 1 note):** `pg_try_advisory_lock` is
session-level; transaction pooling releases the connection between statements
and drops the lock. The `worker` service env keeps `POSTGRES_HOST=postgres`.

---

## 4. Remove the singleton jobs from the web lifespan

So they don't run on every web pod **and** in the worker. In
`backend/app/main.py`'s lifespan, remove the three `asyncio.create_task(...)`
lines for the daily jobs (they call `run_dlp_retention_cleanup`,
`run_log_archive`, `run_partition_maintenance`):

```python
# REMOVE from lifespan (now owned by backend/worker.py):
#   retention_task = asyncio.create_task(_daily_dlp_retention())
#   archive_task   = asyncio.create_task(_daily_log_archive())
#   partition_task = asyncio.create_task(_daily_partition_maintenance())
```

**KEEP** the per-pod observability tasks — `_loop_monitor`, `_db_pool_sampler`,
and `_alert_evaluation_sweep` measure *each* pod and must stay in the web
process. (Model discovery can stay per-pod — idempotent upserts — or move to the
worker later.)

Until you do this, the worker + lifespan both run the jobs: redundant but safe
(all three are idempotent).

---

## 5. Front everything with nginx + rebuild admin-ui's base URL

nginx (`:80`) catch-all-proxies to the gateway (which proxies to Python). To
make nginx the sole entry point, remove the gateway's host port so only nginx is
public:

```yaml
# gateway service:
#   ports: [ "8080:8080" ]   # REMOVE
    expose: [ "8080" ]        # internal only
```

**Blast radius — admin-ui:** its API base URL is **baked into the browser bundle
at build time** (`NEXT_PUBLIC_API_BASE_URL`, a docker build arg). With the
gateway no longer on host `:8080`, you must **rebuild admin-ui** pointing at the
nginx origin:

```yaml
# admin-ui build args + environment:
NEXT_PUBLIC_API_BASE_URL: https://your-host        # was http://localhost:8080
```
```bash
docker compose build admin-ui && docker compose --profile scaling up -d
```

**Portal is safe** — it uses a server-side `/api` rewrite via the internal
`BACKEND_URL`, not a baked browser URL.

Add TLS by uncommenting the `443`/cert lines in `infra/nginx/nginx.conf` and the
`nginx` service, and mounting `infra/nginx/certs/`.

If you also want nginx to front chat-edge (`:4000`) or web-chat (`:3002`), add
explicit `location /api/` and `/chat/` blocks — they are NOT behind the gateway.

---

## 6. PgBouncer auth note

`pgbouncer.ini` uses `auth_type = scram-sha-256` with a plaintext `userlist.txt`
generated by `entrypoint.sh` (PgBouncer derives the SCRAM proof). If the image's
`/etc/pgbouncer` is read-only or perms bite, either write the userlist to a
writable path and update `auth_file`, or (dev only, internal network) switch
`auth_type = trust`.

Verify:
```bash
docker compose --profile scaling up -d pgbouncer
docker compose exec pgbouncer psql "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:6432/${POSTGRES_DB}" -c "SELECT 1"
# pool stats:
docker compose exec pgbouncer psql "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:6432/pgbouncer" -c "SHOW POOLS"
```

---

## 7. Rollback

Every step is independent and reversible:
- **Backend ↔ Postgres:** set `POSTGRES_HOST=postgres POSTGRES_PORT=5432`, `up -d backend`.
- **Worker double-run:** restore the lifespan tasks (step 4) or just `docker compose stop worker`.
- **nginx:** restore the gateway's `8080:8080` host port and rebuild admin-ui with `http://localhost:8080`; `docker compose stop nginx`.
- **Tear down all scaling infra:** `docker compose --profile scaling down` (leaves the default stack running).

---

## Activation order (summary)

1. `docker compose --profile scaling up -d pgbouncer` → verify (step 6).
2. db.py `prepare_threshold=None` behind `DB_PGBOUNCER_MODE=1` (step 2).
3. Dockerfile migration bypass (step 3).
4. Backend env → `pgbouncer:6432` + smaller pools + `DB_PGBOUNCER_MODE=1` (step 1).
5. Remove lifespan jobs; bring up `worker` (step 4).
6. nginx + admin-ui rebuild; remove gateway host port (step 5).
