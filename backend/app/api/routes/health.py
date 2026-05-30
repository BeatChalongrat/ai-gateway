import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.db import engine
from app.core.config import settings
from app.schemas.common import HealthResponse

router = APIRouter()

# Cache health result for 10s to prevent worker exhaustion from rapid polling.
# Each uncached call does sync DB connect + Redis connect (2s timeout if Redis down).
_health_cache: dict | None = None
_health_cache_ts: float = 0.0
_HEALTH_CACHE_TTL = 10.0

# Re-audit H5: which sidecars are on the production path, keyed by engine
# flag. /health probes only the ACTIVE ones — an active sidecar being
# unreachable means we're silently failing open, which must surface as
# "degraded" (not a green health). Inactive engines report "not_in_use".
#   (kind, address_attr, engine_attr, active_engine_values, fallback)
_SIDECARS = (
    ("dlp_sidecar", "dlp_grpc_address", "dlp_engine", ("rust", "shadow"), "python"),
    ("rate_limiter", "rate_limiter_grpc_address", "rate_limiter_engine", ("rust",), "python"),
    ("log_worker", "log_worker_grpc_address", "log_worker_engine", ("go",), "python-inline"),
)


def _probe_sidecar(kind: str, addr: str, timeout: float = 0.5) -> str:
    """Synchronous gRPC Health probe. Returns 'healthy' | 'unreachable'.

    Runs inside ``_run_health_checks`` (already off the event loop via
    ``to_thread``). A short-lived sync channel is opened + closed per
    probe; any error (timeout, connect refused, stub import) → unreachable.
    """
    channel = None
    try:
        import grpc

        if kind == "dlp_sidecar":
            from app.services.dlp import dlp_pb2 as pb, dlp_pb2_grpc as pbg
            channel = grpc.insecure_channel(addr)
            pbg.DlpServiceStub(channel).Health(pb.HealthRequest(), timeout=timeout)
        elif kind == "rate_limiter":
            from app.services.rate_limiter_client import (
                ratelimit_pb2 as pb,
                ratelimit_pb2_grpc as pbg,
            )
            channel = grpc.insecure_channel(addr)
            pbg.RateLimitServiceStub(channel).Health(pb.HealthRequest(), timeout=timeout)
        elif kind == "log_worker":
            from app.services.log_worker_client import (
                logworker_pb2 as pb,
                logworker_pb2_grpc as pbg,
            )
            channel = grpc.insecure_channel(addr)
            pbg.LogWorkerServiceStub(channel).Health(pb.HealthRequest(), timeout=timeout)
        else:
            return "unknown"
        return "healthy"
    except Exception:
        return "unreachable"
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass


@router.get("/health", response_model=HealthResponse)
async def health():
    global _health_cache, _health_cache_ts

    now = time.time()
    if _health_cache is None or (now - _health_cache_ts) >= _HEALTH_CACHE_TTL:
        # Run the blocking checks in a thread to avoid blocking the event loop
        _health_cache = await asyncio.to_thread(_run_health_checks)
        _health_cache_ts = time.time()

    result = _health_cache
    # DB/Redis down → 503 so orchestrators see the failure; sidecar
    # unreachable is "degraded" (fail-open still serves) → 200.
    status_code = 503 if result["status"] == "unhealthy" else 200
    return JSONResponse(status_code=status_code, content=result)


def _run_health_checks() -> dict:
    checks = {}

    # DB check
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # DB pool stats — lets us see if the pool is exhausted at the moment
    # of the health check. If checked_out hits size+overflow
    # everything else will block for up to pool_timeout (10s).
    try:
        pool = engine.pool
        checks["db_pool"] = {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "checked_in": pool.checkedin(),
        }
    except Exception as e:
        checks["db_pool"] = f"error: {e}"

    # Redis check — short timeout so it doesn't block workers
    try:
        import redis
        r = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        r.ping()
        checks["redis"] = "ok"
        r.close()
    except Exception:
        checks["redis"] = "unavailable"

    # Sidecar reachability (H5). Probe only ACTIVE engines; an active
    # sidecar being down means we're failing open → degraded.
    sidecars: dict = {}
    any_active_sidecar_down = False
    for kind, addr_attr, engine_attr, active_vals, fallback in _SIDECARS:
        engine_val = getattr(settings, engine_attr, "python")
        if engine_val not in active_vals:
            sidecars[kind] = {"status": "not_in_use", "engine": engine_val}
            continue
        st = _probe_sidecar(kind, getattr(settings, addr_attr))
        sidecars[kind] = {"status": st, "engine": engine_val, "fallback": fallback}
        if st != "healthy":
            any_active_sidecar_down = True
    checks["sidecars"] = sidecars

    db_ok = checks["database"] == "ok"
    if not db_ok:
        overall = "unhealthy"
    elif checks["redis"] != "ok" or any_active_sidecar_down:
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "service": "secure-ai-gateway",
        "version": "0.1.0",
        "checks": checks,
    }
