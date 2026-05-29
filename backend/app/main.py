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
from app.api.routes.admin import router as admin_router
from app.api.routes.portal_auth import router as portal_auth_router
from app.api.routes.portal_me import router as portal_me_router
from app.api.routes.portal_projects import router as portal_projects_router
from app.api.routes.portal_billing import router as portal_billing_router
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
    DB_POOL_ACTIVE,
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

    # Set explicit thread pool — prevents exhaustion from asyncio.to_thread() calls.
    # Default is min(32, cpu+4) which can be as low as 5 on small machines.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)
    asyncio.get_event_loop().set_default_executor(executor)

    # Seed admin user (runs blocking bcrypt in thread)
    await asyncio.to_thread(_seed_admin_user)

    # Import env-configured provider API keys into provider_keys.
    # Without this, OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY
    # are reachable to the routing path but invisible to the admin UI —
    # the Providers page surfaces them as an opaque "via env" badge that
    # cannot be tested or rotated. Importing them once at boot gives the
    # operator a real row to manage and unifies key management behind a
    # single source.
    await asyncio.to_thread(_import_env_provider_keys)

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

    # DB pool sampler — every 5s reads SQLAlchemy pool stats and
    # pushes them to the Prometheus gauge. Load tests consume this
    # via /metrics to correlate saturation with pool contention.
    pool_task = asyncio.create_task(_db_pool_sampler())

    # Model registry: swap the import-time seed snapshot for the
    # current DB contents, then start the APScheduler job that
    # refreshes ``model_configs`` daily. Failures here are logged
    # and swallowed — the seed snapshot is a valid fallback and
    # the gateway must still serve requests on a cold DB.
    await asyncio.to_thread(_initial_registry_reload)
    _start_model_discovery_scheduler()

    # Phase-1 async request-log queue. Starts the background worker
    # that drains the queue every ~100ms or whenever a 50-record batch
    # is ready. Until this completes ``enqueue()`` falls back to a
    # synchronous persist on the caller's session, so requests served
    # during the startup window still get logged correctly.
    from app.services import request_log_queue
    await request_log_queue.start_worker()

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
    _stop_model_discovery_scheduler()
    monitor_task.cancel()
    retention_task.cancel()
    archive_task.cancel()
    partition_task.cancel()
    pool_task.cancel()
    executor.shutdown(wait=False)


def _seed_admin_user() -> None:
    """Sync function — runs in a thread via asyncio.to_thread.

    Auto-seeds an admin row on first boot of an empty DB so a fresh
    `docker compose up` is usable without a separate `make seed`.

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


def _import_env_provider_keys() -> None:
    """Import env-configured provider API keys into ``provider_keys``.

    Sync function — runs in a thread via ``asyncio.to_thread``. Idempotent:
    skips any provider whose ``provider_keys`` row set already contains a
    key (active or soft-deleted), so a reboot never duplicates, and never
    overrides a key the operator added manually through the admin UI. If
    the operator soft-deletes our imported key, the soft-deleted row keeps
    us from re-creating it on the next boot — the deletion stands.

    Keys are inserted with no test status; the admin UI's Test button
    populates ``last_test_status`` on demand.
    """
    from app.core.db import SessionLocal
    from app.services.provider_key_service import ProviderKeyService

    env_keys = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
    }

    session = SessionLocal()
    try:
        svc = ProviderKeyService()
        imported: list[str] = []
        for provider, raw_key in env_keys.items():
            raw_key = (raw_key or "").strip()
            if not raw_key:
                continue
            if svc.list_keys(session, provider):
                # Provider already has at least one row (active or
                # historically soft-deleted) — leave it alone.
                continue
            svc.add_key(
                session,
                provider,
                raw_key,
                label="Imported from environment",
            )
            imported.append(provider)
        session.commit()
        if imported:
            print(
                "[startup] Imported env API keys into provider_keys: "
                + ", ".join(imported)
            )
    except Exception as exc:
        session.rollback()
        print(f"[startup] env-key import skipped: {exc}")
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
        if lag > 0.1:
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
# Lightweight ASGI middlewares (no BaseHTTPMiddleware — avoids worker starvation)
# NOTE: add_middleware stacks inside-out — last added runs first on the request path.
# Order on the wire: BodySizeLimit -> RequestContext -> RequestTiming -> Timeout -> Metrics
# RequestTiming sits outside Timeout so it still logs a line when TimeoutMiddleware
# fires a 504, and inside BodySizeLimit/RequestContext so request_id is already set.
app.add_middleware(MetricsMiddleware)
app.add_middleware(TimeoutMiddleware, timeout_seconds=20.0)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(RequestContextMiddleware)
# CSRF check for /admin/* mutations — runs after routing is
# established so the path is available to the middleware. See
# app/middleware/csrf.py for the scope rules.
app.add_middleware(CsrfMiddleware)
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=1 * 1024 * 1024,
    path_overrides={"/portal/me/avatar": 5 * 1024 * 1024},
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
app.include_router(auth_router)  # /auth/login → 410, /auth/refresh, /auth/me
from app.api.routes.admin_session import router as admin_session_router
app.include_router(admin_session_router)  # /auth/session/{login,logout,me}
app.include_router(public_chat_router, prefix="/v1")
app.include_router(public_models_router, prefix="/v1")
app.include_router(admin_router, prefix="/admin")

# Portal (self-service) — separate JWT secret; see portal_auth.py
app.include_router(portal_auth_router, prefix="/portal/auth", tags=["portal-auth"])
app.include_router(portal_me_router, prefix="/portal/me", tags=["portal-me"])
app.include_router(portal_projects_router, prefix="/portal/projects", tags=["portal-projects"])
app.include_router(portal_billing_router, prefix="/portal/billing", tags=["portal-billing"])
app.include_router(portal_team_router, prefix="/portal/team", tags=["portal-team"])

# Admin views over portal data
app.include_router(admin_portal_users_router, prefix="/admin/portal-users", tags=["admin-portal"])
app.include_router(admin_system_settings_router, prefix="/admin/system-settings", tags=["admin-settings"])
app.include_router(admin_token_packs_router, prefix="/admin/token-packs", tags=["admin-packs"])

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
