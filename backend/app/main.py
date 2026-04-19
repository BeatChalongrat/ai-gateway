from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.public_chat import router as public_chat_router
from app.api.routes.public_models import router as public_models_router
from app.api.routes.admin import router as admin_router
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
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.request_timing import RequestTimingMiddleware
from app.middleware.timeout import TimeoutMiddleware
from app.middleware.metrics import MetricsMiddleware, get_metrics
from fastapi.middleware.cors import CORSMiddleware

configure_logging()


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

    yield

    monitor_task.cancel()
    retention_task.cancel()
    executor.shutdown(wait=False)


def _seed_admin_user() -> None:
    """Sync function — runs in a thread via asyncio.to_thread."""
    import uuid
    import bcrypt
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.admin_user import AdminUserORM

    session = SessionLocal()
    try:
        existing = session.execute(
            select(AdminUserORM).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return

        hashed = bcrypt.hashpw(b"admin1234", bcrypt.gensalt()).decode("utf-8")
        admin = AdminUserORM(
            id=str(uuid.uuid4()),
            username="admin",
            email="admin@example.com",
            hashed_password=hashed,
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.commit()
        print("[startup] Seeded default admin user: admin / admin1234")
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


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

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
app.add_middleware(BodySizeLimitMiddleware, max_bytes=1 * 1024 * 1024)

# --- Exception handlers (order: most specific -> least specific) ---
app.add_exception_handler(GatewayError, handle_gateway_error)
app.add_exception_handler(RequestValidationError, handle_validation_error)
app.add_exception_handler(StarletteHTTPException, handle_http_exception)
app.add_exception_handler(Exception, handle_unhandled_exception)

# --- Routers ---
app.include_router(health_router)
app.include_router(auth_router)  # /auth/login, /auth/refresh, /auth/me
app.include_router(public_chat_router, prefix="/v1")
app.include_router(public_models_router, prefix="/v1")
app.include_router(admin_router, prefix="/admin")


@app.get("/metrics")
async def metrics():
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
