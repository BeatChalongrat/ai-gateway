from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.public_chat import router as public_chat_router
from app.api.routes.public_models import router as public_models_router
from app.api.routes.public_images import router as public_images_router
from app.api.routes.public_search import router as public_search_router
from app.api.routes.admin import router as admin_router
from app.api.routes.portal_auth import router as portal_auth_router
from app.api.routes.portal_me import router as portal_me_router
from app.api.routes.portal_projects import router as portal_projects_router
from app.api.routes.portal_billing import router as portal_billing_router
from app.api.routes.portal_connectors import router as portal_connectors_router
from app.api.routes.portal_team import router as portal_team_router
from app.api.routes.admin_portal_users import router as admin_portal_users_router
from app.api.routes.admin_system_settings import router as admin_system_settings_router
from app.api.routes.admin_token_packs import router as admin_token_packs_router
from app.core.config import settings
from app.core.exceptions import GatewayError
from app.core.logging import configure_logging
from app.middleware.error_handler import (
    handle_gateway_error,
    handle_http_exception,
    handle_unhandled_exception,
    handle_validation_error,
)
from app.middleware.body_limit import BodySizeLimitMiddleware
from app.middleware.csrf import CsrfMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.request_timing import RequestTimingMiddleware
from app.middleware.timeout import TimeoutMiddleware
from app.middleware.metrics import MetricsMiddleware, get_metrics
from app.observability.metrics import (
    ASYNC_DB_POOL_CHECKED_OUT,
    ASYNC_DB_POOL_MAX,
    ASYNC_DB_POOL_OVERFLOW,
    DB_POOL_ACTIVE,
    DB_POOL_MAX,
    DB_POOL_OVERFLOW,
    EVENT_LOOP_BLOCK_SECONDS,
    EVENT_LOOP_BLOCK_TOTAL,
    render_prometheus_exposition,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.core.responses import UTF8JSONResponse

configure_logging()

# Fail loud + early if any security-critical secret is a known-insecure
# default or below the minimum byte length. In dev/test this is a no-op;
# in production it crashes the worker before a single request is served.
settings.validate_secrets()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Seed default admin user on startup, start event loop monitor."""
    import asyncio
    import concurrent.futures
    import os as _os

    # Debug-only memory profiling. Inert unless ENABLE_TRACEMALLOC=true; when
    # on, start tracing AS EARLY AS POSSIBLE so baseline snapshots capture the
    # warm import set. Snapshots are taken via the gated internal_debug router.
    if _os.environ.get("ENABLE_TRACEMALLOC", "").lower() in ("1", "true", "yes"):
        import tracemalloc as _tm

        if not _tm.is_tracing():
            _tm.start(int(_os.environ.get("TRACEMALLOC_NFRAME", "15")))
        print("tracemalloc_enabled nframe="
              f"{_os.environ.get('TRACEMALLOC_NFRAME', '15')}", flush=True)

    # Set explicit thread pool — prevents exhaustion from asyncio.to_thread() calls.
    # Default is min(32, cpu+4) which can be as low as 5 on small machines.
    # Track A: the chat hot-path DB reads are now NATIVE async (the flip
    # removed the Phase-2 DB to_thread hops), so the executor no longer needs
    # to absorb ~50 concurrent DB calls. What still uses to_thread:
    # DLP preprocessing (H13) + semantic embed (B4) — both semaphore-bounded
    # (DLP 8, semantic 4) — plus a few one-off startup/bcrypt offloads. 20
    # threads comfortably covers those (down from the Phase-2 hybrid's 48).
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=20)
    asyncio.get_event_loop().set_default_executor(executor)

    # Schema-drift guard (FIX 3) — BEFORE any DB-dependent startup work or
    # serving traffic. If the DB schema is behind the code's Alembic head
    # (the hot-reload drift that made every request 500 on a missing
    # column), either auto-upgrade in-process (dev, advisory-locked) or
    # refuse to start with a named, actionable error (prod). Runs in a
    # thread — it uses the sync engine + Alembic's sync command API.
    from app.core.schema_guard import run_schema_guard
    await asyncio.to_thread(run_schema_guard)

    # Service-JWT wiring visibility (FIX 1b) — make a DORMANT decoupling loud
    # in every env. validate_secrets() already REFUSES to start in prod when
    # chat-edge is wired but SERVICE_JWT_SECRET is empty; this covers dev/test
    # (where validate_secrets no-ops) so the silent fallback can't hide.
    _jwt_issue = settings.service_jwt_wiring_issue()
    if _jwt_issue:
        print(f"[startup] WARNING: {_jwt_issue}", flush=True)
    elif (settings.service_jwt_secret or "").strip():
        print("[startup] service-JWT auth ACTIVE (chat-edge service tokens "
              "verified against SERVICE_JWT_SECRET).", flush=True)

    # Seed admin user (runs blocking bcrypt in thread)
    await asyncio.to_thread(_seed_admin_user)

    # Per-worker NER pre-load. ``ner_status`` module-level state is
    # process-local — a ``PATCH /admin/settings`` call routes to one
    # gunicorn worker, which loads the analyzer in *its* process only,
    # leaving the other workers at ``not_loaded``. Subsequent chat
    # requests can then land on a worker that has no analyzer and
    # ``PresidioDetector`` silently no-ops for them. We fix that here:
    # each worker checks ``org_settings.ner_enabled`` on boot and loads
    # its own analyzer when the DB flag is on.
    await asyncio.to_thread(_maybe_preload_ner)

    # Start event loop lag monitor — logs when loop is blocked > 100ms
    monitor_task = asyncio.create_task(_loop_monitor())

    # Daily DLP retention — NULL-ifies raw PII columns on request_logs
    # rows older than ``settings.dlp_raw_retention_days``. Only one
    # gunicorn worker needs to run this, but every worker scheduling
    # it is acceptable: the DB update is idempotent (already-NULL rows
    # are a no-op) and the cost is one SQL UPDATE per day per worker.
    # If that becomes a concern, promote to a distributed lock.
    retention_task = asyncio.create_task(_daily_dlp_retention())

    # Daily archive — moves request_logs rows older than
    # ``settings.log_archive_days`` into ``request_logs_archive``.
    # Same pattern as DLP retention: cancel-safe, idempotent, sleeps
    # 24h between runs. See app/jobs/log_archive_job.py.
    archive_task = asyncio.create_task(_daily_log_archive())

    # Monthly partition maintenance — creates next month's partition
    # if it doesn't exist yet. Sleeps 24h between checks; the CREATE
    # statement itself is idempotent so the actual frequency doesn't
    # matter as long as it runs at least once before each month rolls.
    partition_task = asyncio.create_task(_daily_partition_maintenance())

    # Sprint 1 — async media-generation job loop. Polls ``generation_jobs``
    # and (once Sprint 2 lands a provider adapter) drives video/music/TTS
    # jobs to completion under advisory lock 900004. Inert-ish today: with
    # no adapter registered it marks any queued job failed('no_adapter').
    # Cancellation-safe like the daily loops above; Postgres-only (idles on
    # other dialects). See app/services/media_gen/job_loop.py.
    from app.services.media_gen.job_loop import run_generation_job_loop
    generation_job_task = asyncio.create_task(run_generation_job_loop())

    # DB pool sampler — every 5s reads SQLAlchemy pool stats and
    # pushes them to the Prometheus gauge. Load tests consume this
    # via /metrics to correlate saturation with pool contention.
    pool_task = asyncio.create_task(_db_pool_sampler())

    # Phase-1 B6: alert rules are evaluated on a periodic background
    # sweep instead of once per completed request. Their metrics are
    # time-window aggregates, so a sweep is equivalent and keeps the
    # enabled-rules SELECT + window aggregates off the request hot path.
    alert_task = asyncio.create_task(_alert_evaluation_sweep())

    # Web-search provider health monitor: probes every enabled search
    # provider across all tenants every 5 minutes, updating health /
    # latency / uptime in the DB and logging outage/recovery transitions.
    # The fallback chain skips providers it marks 'down'.
    from app.services.search.health import search_health_monitor_loop
    search_health_task = asyncio.create_task(search_health_monitor_loop())

    # Model registry: swap the import-time seed snapshot for the
    # current DB contents, then start the APScheduler job that
    # refreshes ``model_configs`` daily. Failures here are logged
    # and swallowed — the seed snapshot is a valid fallback and
    # the gateway must still serve requests on a cold DB.
    await asyncio.to_thread(_initial_registry_reload)
    _start_model_discovery_scheduler()

    # Sprint 6: admin-configurable custom DLP detectors. Build the initial
    # snapshot, subscribe to the Redis reload channel, and install the
    # process-wide provider so the detectors run alongside the built-ins.
    # Best-effort: a failure here must not stop the gateway booting (custom
    # detectors are additive; the 31 built-ins still run).
    custom_detector_loader = None
    try:
        from app.core.db import get_async_session_factory
        from app.services._redis_pool import get_async_redis
        from app.services.dlp.custom_detector_loader import CustomDetectorLoader
        from app.services.dlp.detectors.registry import set_global_custom_provider

        custom_detector_loader = CustomDetectorLoader(
            get_async_redis(), get_async_session_factory()
        )
        await custom_detector_loader.start()
        set_global_custom_provider(custom_detector_loader.get_cached_detectors)
        app.state.custom_detector_loader = custom_detector_loader
    except Exception as exc:  # noqa: BLE001 — additive feature, never block boot
        print(f"custom_detector_loader_start_skipped error={exc}", flush=True)

    # Phase-1 async request-log queue. Starts the background worker
    # that drains the queue every ~100ms or whenever a 50-record batch
    # is ready. Until this completes ``enqueue()`` falls back to a
    # synchronous persist on the caller's session, so requests served
    # during the startup window still get logged correctly.
    from app.services import request_log_queue
    await request_log_queue.start_worker()

    # Re-audit H5: probe active sidecars at boot and log their status, so
    # a misconfigured DLP_ENGINE=rust against a down sidecar is visible in
    # the boot log (it would otherwise silently fail open per request).
    await asyncio.to_thread(_log_sidecar_status_at_boot)

    # Track A: pre-warm the async engine + one pooled connection at startup.
    # ``get_async_engine`` builds lazily on first use; without this, the
    # FIRST burst of chat requests after a (re)start all race the lazy build
    # + cold pool fill, which the A.6 benchmark caught as a thundering-herd
    # (first tier: ~14s latency + timeouts). One warm connection here makes
    # the engine + pool ready before the first request lands.
    try:
        from sqlalchemy import text as _text
        from app.core.db import get_async_session_factory

        async with get_async_session_factory()() as _warm:
            await _warm.execute(_text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — best-effort warm-up
        print(f"async_engine_prewarm_skipped error={exc}", flush=True)

    yield

    # Drain + stop the queue first so any in-flight records make it
    # to Postgres before the worker pool tears down.
    await request_log_queue.stop_worker()
    # Phase-1 B1: close the pooled provider HTTP clients.
    try:
        from app.services.providers.http_client import close_all_clients
        await close_all_clients()
    except Exception:  # noqa: BLE001
        pass
    # Phase-2: dispose the async DB engine if it was ever built.
    try:
        from app.core.db import dispose_async_engine
        await dispose_async_engine()
    except Exception:  # noqa: BLE001
        pass
    _stop_model_discovery_scheduler()
    monitor_task.cancel()
    retention_task.cancel()
    archive_task.cancel()
    alert_task.cancel()
    search_health_task.cancel()
    partition_task.cancel()
    generation_job_task.cancel()
    pool_task.cancel()
    # Sprint 6: stop the custom-detector loader + clear the global provider.
    try:
        from app.services.dlp.detectors.registry import set_global_custom_provider

        set_global_custom_provider(None)
        if custom_detector_loader is not None:
            await custom_detector_loader.stop()
    except Exception:  # noqa: BLE001
        pass
    executor.shutdown(wait=False)


# --- Startup seed serialization (Phase 12 A1) ------------------------------
# Advisory-lock ids for the one-time startup seed/import steps below. Chosen
# in the 9000xx block the other singleton jobs already use: worker.py owns
# 900001-900003 and the media-generation job loop owns 900004 (see
# app/services/media_gen/job_loop.py) — grep for "pg_advisory" before adding
# a new id. Scope note: the job loops take SESSION-level
# ``pg_try_advisory_lock`` because a claim spans several transactions; each
# seed step below runs its whole check-then-insert inside ONE transaction,
# so the transaction-scoped ``pg_advisory_xact_lock`` is the right variant —
# a second worker blocks until the first worker's COMMIT makes the seeded
# row visible, and the lock releases automatically with the transaction (no
# cross-connection unlock bookkeeping after the pool recycles the
# connection).
STARTUP_LOCK_SEED_ADMIN = 900005
# 900006 was STARTUP_LOCK_ENV_KEY_IMPORT (env-key importer, removed). Left
# documented, not reused, so an old worker mid-rollout cannot collide.


def _acquire_startup_lock(session, lock_id: int) -> None:
    """Serialize a startup check-then-insert step across workers.

    Blocks on ``pg_advisory_xact_lock`` so only one of the N gunicorn
    workers runs the seed/import at a time; the others wait, then see
    the committed row and no-op. Without this, 4 workers booting on a
    fresh DB all pass the "does the row exist?" check before any of
    them commits, and the losers double-insert (or crash into the
    unique constraint and abort the whole import batch).

    On non-Postgres dialects (SQLite tests) advisory locks don't
    exist, so this is a no-op — tests simulate the mutual exclusion
    by monkeypatching this helper.
    """
    from sqlalchemy import text

    try:
        dialect = session.bind.dialect.name
    except Exception:  # noqa: BLE001 — best-effort dialect probe
        return
    if dialect != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id}
    )


def _seed_admin_user() -> None:
    """Sync function — runs in a thread via asyncio.to_thread.

    Auto-seeds an admin row on first boot of an empty DB so a fresh
    `docker compose up` is usable without a separate `make seed`.
    Serialized across workers via advisory lock 900005 (see
    ``_acquire_startup_lock``) so a 4-worker boot on a fresh DB seeds
    exactly once.

    Outside of APP_ENV=development this is gated on
    INITIAL_ADMIN_PASSWORD being set in the environment. If it's not
    set we skip seeding — the operator must run `make seed`
    explicitly with the env populated, which raises a clearer error.
    Refusing to silently plant a weak credential is what closed the
    production incident where `admin/admin1234` survived deploy.
    """
    import os
    import uuid
    import bcrypt
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.admin_user import AdminUserORM

    app_env = os.environ.get("APP_ENV", "development").lower()
    admin_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
    if not admin_password:
        if app_env != "development":
            print(
                "[startup] Admin auto-seed skipped: INITIAL_ADMIN_PASSWORD "
                "not set and APP_ENV != 'development'. Run `make seed` "
                "with INITIAL_ADMIN_PASSWORD in the environment."
            )
            return
        admin_password = "admin1234"
    admin_username = os.environ.get("INITIAL_ADMIN_USERNAME", "admin")
    admin_email = os.environ.get("INITIAL_ADMIN_EMAIL", "admin@example.com")

    session = SessionLocal()
    try:
        # Take the transaction-scoped advisory lock BEFORE the existence
        # check; released automatically at COMMIT/ROLLBACK (Phase 12 A1).
        _acquire_startup_lock(session, STARTUP_LOCK_SEED_ADMIN)
        existing = session.execute(
            select(AdminUserORM).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return

        hashed = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        admin = AdminUserORM(
            id=str(uuid.uuid4()),
            username=admin_username,
            email=admin_email,
            hashed_password=hashed,
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.commit()
        # Never log the password — print only that the seed happened so
        # ops know a fresh DB was just populated.
        print(f"[startup] Seeded default admin user: {admin_username}")
    except Exception as e:
        session.rollback()
        print(f"[startup] Admin seed skipped: {e}")
    finally:
        session.close()


def _maybe_preload_ner() -> None:
    """If ``org_settings.ner_enabled`` is True, load the Presidio
    analyzer into *this* worker's process. No-op otherwise.

    Logs outcome to stdout so operators see the boot-time state in
    container logs without having to hit the admin API from each
    worker.
    """
    from app.core.db import SessionLocal
    from app.services.dlp import ner_status
    from app.models.org_settings import OrgSettingsORM

    session = SessionLocal()
    try:
        row = session.get(OrgSettingsORM, 1)
        if row is None or not getattr(row, "ner_enabled", False):
            return
        ner_status.begin_load()
        ner_status.load_analyzer_sync()
        status = ner_status.get_status()
        print(
            f"[startup] NER pre-load: status={status.status} "
            f"message={status.message or ''}"
        )
    except Exception as exc:
        print(f"[startup] NER pre-load skipped: {exc}")
    finally:
        session.close()


def _log_sidecar_status_at_boot() -> None:
    """Probe active polyglot sidecars once at startup and log status.

    Audit H5: an active engine flag (DLP_ENGINE=rust, RATE_LIMITER_ENGINE=
    rust, LOG_WORKER_ENGINE=go) pointed at an unreachable sidecar otherwise
    fails open silently on every request. Surfacing it at boot gives the
    operator a clear signal. Best-effort — never blocks startup.
    """
    import structlog

    from app.api.routes.health import _SIDECARS, _probe_sidecar
    from app.core.config import settings as _s

    log = structlog.get_logger("startup")
    for kind, addr_attr, engine_attr, active_vals, _fallback in _SIDECARS:
        engine_val = getattr(_s, engine_attr, "python")
        if engine_val not in active_vals:
            continue
        addr = getattr(_s, addr_attr)
        status = _probe_sidecar(kind, addr)
        if status == "healthy":
            log.info("sidecar_active", sidecar=kind, engine=engine_val, address=addr)
        else:
            log.warning(
                "sidecar_unreachable_failing_open",
                sidecar=kind,
                engine=engine_val,
                address=addr,
                note="engine flag is active but sidecar is unreachable; "
                "requests will fall back to the in-process path",
            )
    # H11: warn if NER coverage is intentionally dropped on the rust path.
    if _s.dlp_engine in ("rust", "shadow") and not _s.dlp_ner_on_rust_path:
        log.warning(
            "dlp_ner_disabled_on_rust_path",
            note="DLP_NER_ON_RUST_PATH=false with DLP_ENGINE=rust — "
            "Presidio NER (PERSON/LOCATION/…) will NOT run; regex only.",
        )


async def _loop_monitor() -> None:
    """Background task that detects event loop blocking.

    If the loop is blocked for > 100ms, logs a warning with the lag.
    """
    import asyncio
    import time

    while True:
        t = time.monotonic()
        await asyncio.sleep(1)
        lag = time.monotonic() - t - 1.0
        # Always record the (non-negative) lag so the histogram carries a
        # baseline distribution; count + log only real >100ms blocks.
        EVENT_LOOP_BLOCK_SECONDS.observe(lag if lag > 0 else 0.0)
        if lag > 0.1:
            EVENT_LOOP_BLOCK_TOTAL.inc()
            print(f"[loop-monitor] Event loop blocked for {lag:.3f}s")


async def _db_pool_sampler() -> None:
    """Sample SQLAlchemy engine pool stats every 5s into DB_POOL_ACTIVE.

    ``pool.checkedout()`` is the count of connections currently held
    outside the pool (i.e. actively used by an in-flight request). The
    gauge is read by Prometheus; the value also turns up in the
    ``/health`` endpoint but only on demand.
    """
    import asyncio

    from app.core.db import engine

    while True:
        try:
            pool = engine.pool
            active = 0
            if hasattr(pool, "checkedout"):
                try:
                    active = pool.checkedout()
                except Exception:
                    active = 0
            DB_POOL_ACTIVE.set(active)
            # Overflow climbing toward (size+max_overflow) is the leading
            # indicator of imminent pool-timeout 500s (audit H4).
            try:
                if hasattr(pool, "overflow"):
                    DB_POOL_OVERFLOW.set(max(0, pool.overflow()))
                size = pool.size() if hasattr(pool, "size") else 0
                max_overflow = getattr(pool, "_max_overflow", 0) or 0
                DB_POOL_MAX.set(size + max(0, max_overflow))
            except Exception:
                pass

            # ASYNC pool — the chat hot path's separate engine. Sampling
            # only its introspection counters (no I/O), and only if the
            # async engine has already been built (it is prewarmed in the
            # lifespan startup). Mirrors the sync gauges above so a
            # checkout-timeout 500 storm (the 150-concurrent failure) is
            # visible BEFORE it happens, via overflow approaching max.
            try:
                from app.core.db import get_async_engine

                apool = get_async_engine().pool
                if hasattr(apool, "checkedout"):
                    ASYNC_DB_POOL_CHECKED_OUT.set(apool.checkedout())
                if hasattr(apool, "overflow"):
                    ASYNC_DB_POOL_OVERFLOW.set(max(0, apool.overflow()))
                asize = apool.size() if hasattr(apool, "size") else 0
                amax_overflow = getattr(apool, "_max_overflow", 0) or 0
                ASYNC_DB_POOL_MAX.set(asize + max(0, amax_overflow))
            except Exception:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let telemetry break the app.
            pass
        await asyncio.sleep(5)


async def _daily_dlp_retention() -> None:
    """Run ``run_dlp_retention_cleanup`` once every 24 hours.

    First pass runs after a 60s warm-up so boot-time traffic doesn't
    compete with an UPDATE over the whole table. All DB work is pushed
    to a thread so the event loop stays responsive even on a cold index
    scan; the retention job itself is a plain blocking function.
    """
    import asyncio

    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(_run_dlp_retention_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[dlp-retention] run failed: {exc}")
        await asyncio.sleep(24 * 60 * 60)


def _initial_registry_reload() -> None:
    """Refresh the in-memory model registry from the DB at startup.

    Runs synchronously in a thread (lifespan dispatches via
    ``asyncio.to_thread``). On any failure the import-time seed
    snapshot remains in place — the registry is never empty.
    """
    import asyncio as _asyncio

    from app.core.db import SessionLocal
    from app.services.providers.model_registry import _registry

    session = SessionLocal()
    try:
        # ``_registry.reload`` is async (it dispatches the query back
        # to a thread for psycopg). We're already on a worker thread
        # so we run it on a fresh event loop just for this call.
        _asyncio.run(_registry.reload(session))
    except Exception as exc:
        print(f"[startup] Model registry reload skipped: {exc}")
    finally:
        session.close()


def _start_model_discovery_scheduler() -> None:
    """Register and start the daily ``model_configs`` sync job."""
    try:
        from app.scheduler import start_scheduler
        from app.services.discovery.discovery_service import (
            ModelDiscoveryService,
        )
        start_scheduler(ModelDiscoveryService())
    except Exception as exc:
        print(f"[startup] Model discovery scheduler skipped: {exc}")


def _stop_model_discovery_scheduler() -> None:
    """Stop the scheduler on shutdown — best-effort, never raises."""
    try:
        from app.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as exc:
        print(f"[shutdown] Scheduler stop skipped: {exc}")


async def _daily_log_archive() -> None:
    """Run the log-archive job once every 24 hours."""
    import asyncio

    # Stagger the start so archive and retention don't both fight the
    # DB on a cold boot — retention starts at +60s, archive at +120s.
    await asyncio.sleep(120)
    while True:
        try:
            await asyncio.to_thread(_run_log_archive_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[log-archive] run failed: {exc}")
        await asyncio.sleep(24 * 60 * 60)


async def _daily_partition_maintenance() -> None:
    """Run partition maintenance once every 24 hours."""
    import asyncio

    await asyncio.sleep(180)  # third in the daily-task stagger
    while True:
        try:
            await asyncio.to_thread(_run_partition_maintenance_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[partition-maint] run failed: {exc}")
        await asyncio.sleep(24 * 60 * 60)


async def _alert_evaluation_sweep() -> None:
    """Phase-1 B6: evaluate alert rules on a periodic background sweep.

    Replaces the per-request ``evaluate_after_request`` call. Alert
    metrics are time-window aggregates keyed on each rule's own scope,
    so a periodic sweep is equivalent and removes the enabled-rules
    SELECT + per-rule window aggregates from the request hot path.
    """
    import asyncio
    from app.core.db import SessionLocal
    from app.services.alert_evaluator import AlertEvaluator

    interval = float(getattr(settings, "alert_sweep_interval_seconds", 10.0))
    evaluator = AlertEvaluator()
    await asyncio.sleep(15)  # let the app settle before the first sweep
    while True:
        try:
            session = SessionLocal()
            try:
                await evaluator.evaluate_all(session)
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                print(f"[alert-sweep] run failed: {exc}")
            finally:
                session.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[alert-sweep] loop error: {exc}")
        await asyncio.sleep(interval)


def _run_log_archive_once() -> None:
    """Open a session, run the archive job, close the session."""
    from app.core.db import SessionLocal
    from app.jobs.log_archive_job import run_log_archive

    session = SessionLocal()
    try:
        run_log_archive(session)
    finally:
        session.close()


def _run_partition_maintenance_once() -> None:
    """Open a session, run the maintenance job, close the session."""
    from app.core.db import SessionLocal
    from app.jobs.partition_maintenance_job import run_partition_maintenance

    session = SessionLocal()
    try:
        run_partition_maintenance(session)
    finally:
        session.close()


def _run_dlp_retention_once() -> None:
    """Open a session, run the cleanup, close the session."""
    import structlog

    from app.core.db import SessionLocal
    from app.jobs.dlp_retention import run_dlp_retention_cleanup

    logger = structlog.get_logger("dlp.retention")

    session = SessionLocal()
    try:
        stats = run_dlp_retention_cleanup(session)
        logger.info(
            "dlp_retention_job_ran",
            rows_cleaned=stats.rows_with_raw_text_cleared,
            findings_stripped=stats.rows_with_findings_stripped,
            retention_days=stats.retention_days,
        )
    finally:
        session.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)

# --- Middleware (outermost first) ---
# Note: CORSMiddleware must be outermost to handle preflight OPTIONS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Idempotency cache for media-generation submit routes (Sprint 1). Only
# REGISTERED when enabled, and even then the middleware no-ops unless an
# X-Idempotency-Key header is present AND the path is allowlisted
# (/v1/videos/, /v1/audio/ — neither exists until Sprint 2). Gated via env
# (IDEMPOTENCY_ENABLED, default on) instead of a settings field so
# core/config.py stays untouched this sprint.
import os as _os_idem

if _os_idem.getenv("IDEMPOTENCY_ENABLED", "true").lower() not in (
    "0",
    "false",
    "no",
):
    from app.middleware.idempotency import IdempotencyMiddleware

    app.add_middleware(IdempotencyMiddleware)
# Lightweight ASGI middlewares (no BaseHTTPMiddleware — avoids worker starvation)
# NOTE: add_middleware stacks inside-out — last added runs first on the request path.
# Order on the wire: BodySizeLimit -> RequestContext -> RequestTiming -> Timeout -> Metrics
# RequestTiming sits outside Timeout so it still logs a line when TimeoutMiddleware
# fires a 504, and inside BodySizeLimit/RequestContext so request_id is already set.
app.add_middleware(MetricsMiddleware)
app.add_middleware(
    TimeoutMiddleware,
    timeout_seconds=20.0,
    path_timeouts={
        "/v1/images/generations": settings.image_generation_timeout_seconds,
        # Voice work on a long clip outruns the 20s default (B1). The
        # middleware matches exact paths, so both fixed voice routes are
        # listed (see public_voice.py — /voice/transcribe, /voice/synthesize
        # mounted under /v1).
        "/v1/voice/transcribe": settings.voice_timeout_seconds,
        "/v1/voice/synthesize": settings.voice_timeout_seconds,
    },
)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(RequestContextMiddleware)
# CSRF check for /admin/* mutations — runs after routing is
# established so the path is available to the middleware. See
# app/middleware/csrf.py for the scope rules.
app.add_middleware(CsrfMiddleware)
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=1 * 1024 * 1024,
    path_overrides={
        "/portal/me/avatar": 5 * 1024 * 1024,
        # Vision turns inline base64 data URLs (current-turn uploads at
        # 2048px + up to 5 replayed generated images at 512px), which
        # exceeds the 1 MB default. Chat is authenticated and
        # rate-limited, so the wider limit is safe.
        "/v1/chat/completions": 20 * 1024 * 1024,
    },
)

# --- Static files (user-uploaded avatars etc.) ---
# Served at ``/static/*``. Underlying directory lives at
# ``backend/uploads/`` and is filesystem-scoped to this deployment.
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
(UPLOAD_DIR / "avatars").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(UPLOAD_DIR)), name="static")

# --- Exception handlers (order: most specific -> least specific) ---
app.add_exception_handler(GatewayError, handle_gateway_error)
app.add_exception_handler(RequestValidationError, handle_validation_error)
app.add_exception_handler(StarletteHTTPException, handle_http_exception)
app.add_exception_handler(Exception, handle_unhandled_exception)

# --- Routers ---
app.include_router(health_router)
# Debug-only tracemalloc router — ONLY mounted when ENABLE_TRACEMALLOC=true,
# and every endpoint additionally requires the internal secret. Off by
# default so the debug surface does not exist in dev/prod.
import os as _os_main
if _os_main.environ.get("ENABLE_TRACEMALLOC", "").lower() in ("1", "true", "yes"):
    from app.api.routes.internal_debug import router as _internal_debug_router

    app.include_router(_internal_debug_router)
app.include_router(auth_router)  # /auth/login → 410, /auth/refresh, /auth/me
from app.api.routes.admin_session import router as admin_session_router
app.include_router(admin_session_router)  # /auth/session/{login,logout,me}
# Phase 4A.2.5 (B2) — internal Go→Vault V2 restore bridge. No prefix (the path
# is /internal/dlp/...). Always mounted, but every request requires the
# internal-service secret AND Vault V2 enabled, else it restores nothing.
from app.api.routes.internal_dlp import router as internal_dlp_router  # noqa: E402
app.include_router(internal_dlp_router, tags=["internal-dlp"])
from app.api.routes.internal_media_jobs import router as internal_media_jobs_router  # noqa: E402
app.include_router(internal_media_jobs_router)  # prefix declared on the router
from app.api.routes.webhooks_replicate import router as webhooks_replicate_router  # noqa: E402
app.include_router(webhooks_replicate_router, prefix="/webhooks", tags=["webhooks"])
app.include_router(public_chat_router, prefix="/v1")
app.include_router(public_models_router, prefix="/v1")
app.include_router(public_images_router, prefix="/v1")
from app.api.routes.public_videos import router as public_videos_router  # noqa: E402
from app.api.routes.public_audio import router as public_audio_router  # noqa: E402
app.include_router(public_videos_router, prefix="/v1")  # /v1/videos/generations + /v1/jobs/{id}
app.include_router(public_audio_router, prefix="/v1")  # /v1/audio/generations
app.include_router(public_search_router, prefix="/v1")  # /v1/search + /v1/search/config
# FIX 2 — gateway-fronted embeddings + voice so chat-edge stops calling OpenAI
# directly (both run behind the DLP raw-egress gate).
from app.api.routes.public_embeddings import router as public_embeddings_router  # noqa: E402
from app.api.routes.public_voice import router as public_voice_router  # noqa: E402
app.include_router(public_embeddings_router, prefix="/v1")  # /v1/embeddings
app.include_router(public_voice_router, prefix="/v1")  # /v1/voice/{transcribe,synthesize}
app.include_router(admin_router, prefix="/admin")

# Portal (self-service) — separate JWT secret; see portal_auth.py
app.include_router(portal_auth_router, prefix="/portal/auth", tags=["portal-auth"])
app.include_router(portal_me_router, prefix="/portal/me", tags=["portal-me"])
app.include_router(portal_projects_router, prefix="/portal/projects", tags=["portal-projects"])
app.include_router(portal_billing_router, prefix="/portal/billing", tags=["portal-billing"])
app.include_router(portal_team_router, prefix="/portal/team", tags=["portal-team"])
# Connector OAuth broker (Desktop Phase 7) — provider tokens stay server-side.
app.include_router(portal_connectors_router, prefix="/portal/connectors", tags=["portal-connectors"])

# Admin views over portal data
app.include_router(admin_portal_users_router, prefix="/admin/portal-users", tags=["admin-portal"])
app.include_router(admin_system_settings_router, prefix="/admin/system-settings", tags=["admin-settings"])
app.include_router(admin_token_packs_router, prefix="/admin/token-packs", tags=["admin-packs"])
from app.api.routes.admin_search import router as admin_search_router  # noqa: E402
app.include_router(admin_search_router, prefix="/admin", tags=["admin-search"])
# Sprint 6: admin-configurable custom DLP detectors (/admin/dlp/custom-detectors)
from app.api.routes.admin_dlp_detectors import router as admin_dlp_detectors_router  # noqa: E402
app.include_router(admin_dlp_detectors_router, prefix="/admin")

# Admin endpoints for the DB-backed model registry (status, sync,
# list, override). Mounted at /admin/models; backed by
# ``model_configs`` and the APScheduler-driven discovery sync.
from app.api.routes.admin_model_discovery import router as admin_model_discovery_router
app.include_router(admin_model_discovery_router, prefix="/admin/models", tags=["admin-models"])

# --- Admin auth gate (Phase 2A Commit 5) ---
# Backfill a default @require_role(PROJECT_ADMIN) marker onto every
# /admin/* route that doesn't already carry one (endpoints decorated
# explicitly during Commits 2-4 keep their markers). Then run the
# boot-check — crashes the worker if anything slipped through.
from app.api.admin_auth import (
    tag_untagged_admin_routes,
    check_admin_routes_decorated,
)
tag_untagged_admin_routes(app)
check_admin_routes_decorated(app)

# --- Portal scope gate (Phase 3 Commit 23) ---
# Mirrors the admin boot-check: every /portal/* route must declare
# @scope_to_portal_tenant, @explicit_cross_portal_tenant(reason=...),
# or @public_portal_route. Boot crashes on an undeclared route.
from app.api.portal_scope import check_portal_routes_decorated

check_portal_routes_decorated(app)


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus text exposition endpoint.

    Content-Type is ``text/plain; version=0.0.4; charset=utf-8`` per
    the Prometheus exposition format. The legacy in-memory counters
    (``uptime_seconds``, ``requests_by_route``, …) are available at
    ``GET /metrics.json`` for the admin UI; the Prometheus scrape
    target is this path.
    """
    body, content_type = render_prometheus_exposition()
    return Response(content=body, media_type=content_type)


@app.get("/metrics.json")
async def metrics_json() -> dict:
    """Legacy JSON metrics view — kept for admin UI + smoke tests."""
    return get_metrics()


def _start_watchdog() -> None:
    """Self-health watchdog — restarts process if /health is unreachable for 30s."""
    import os
    import sys
    import threading
    import time
    import urllib.request

    def _watchdog_loop():
        consecutive_failures = 0
        while True:
            time.sleep(10)
            try:
                urllib.request.urlopen("http://localhost:8080/health", timeout=5)
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                print(f"[watchdog] Health check failed ({consecutive_failures}/3)")
                if consecutive_failures >= 3:
                    print("[watchdog] 3 consecutive failures — restarting process")
                    os._exit(1)  # Let the process manager restart us

    t = threading.Thread(target=_watchdog_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    import uvicorn

    _start_watchdog()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        workers=4,
        reload=False,
        timeout_keep_alive=5,
        limit_concurrency=100,
    )
