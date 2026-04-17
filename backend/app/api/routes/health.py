import asyncio
import time

from fastapi import APIRouter
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


@router.get("/health", response_model=HealthResponse)
async def health():
    global _health_cache, _health_cache_ts

    now = time.time()
    if _health_cache is not None and (now - _health_cache_ts) < _HEALTH_CACHE_TTL:
        return _health_cache

    # Run the blocking checks in a thread to avoid blocking the event loop
    result = await asyncio.to_thread(_run_health_checks)
    _health_cache = result
    _health_cache_ts = time.time()
    return result


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
    # of the health check. If checked_out hits size+overflow (10+20=30)
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

    db_ok = checks["database"] == "ok"
    overall = "ok" if db_ok else "degraded"
    if checks["redis"] != "ok" and db_ok:
        overall = "degraded"

    return {
        "status": overall,
        "service": "secure-ai-gateway",
        "version": "0.1.0",
        "checks": checks,
    }
