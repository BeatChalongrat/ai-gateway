import asyncio
import time

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.db import engine
from app.core.config import settings
from app.schemas.common import HealthResponse

router = APIRouter()

# Cache health result for 10s to prevent worker exhaustion from rapid polling.
# Each uncached call does sync DB connect + Redis connect (2s timeout if Redis down).
_hlog = structlog.get_logger(__name__)

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


def _probe_ner_service(timeout: float = 0.5) -> dict:
    """FIX38 A7 — is the ner-service up, and did its Thai tagger load?

    The Thai NER layer degrades to "no findings" when the service is down
    (by design: regex keeps running). That is the right failure mode for
    a request and the wrong one for an operator, who otherwise learns
    about it from a leaked name. So the health page says it out loud:
    the probe reads the service's own /health, which reports the Thai
    tagger separately (``th_ner=ready|disabled|error: …``).
    """
    url = str(getattr(settings, "ner_service_url", "") or "")
    thai_on = bool(getattr(settings, "dlp_thai_ner_enabled", False))
    if not url:
        return {"status": "not_configured", "thai_ner_layer": thai_on}
    try:
        import httpx

        r = httpx.get(url.rstrip("/") + "/health", timeout=timeout)
        data = r.json() if r.status_code == 200 else {}
        model = str(data.get("model", ""))
        if "th_ner=ready" in model:
            th = "ready"
        elif "th_ner=disabled" in model:
            th = "disabled"
        else:
            th = "not_ready"
        status = "healthy" if data.get("status") == "ready" else f"degraded ({data.get('status', r.status_code)})"
        return {"status": status, "th_ner": th, "model": model, "thai_ner_layer": thai_on}
    except Exception as exc:  # noqa: BLE001 — a probe never raises
        return {"status": "unreachable", "error": f"{type(exc).__name__}", "thai_ner_layer": thai_on}


# ── FIX26 §2.2 — liveness vs readiness ──────────────────────────────────
#
# /health (above/below) is a cached, mostly-sync component check: during
# the FIX24 wedge it answered 200 for the whole outage because the DB and
# Redis were fine — the chat PATH was stuck. So:
#
#   /live   — the process is up and its event loop turns. Constant.
#   /ready  — a chat request could complete NOW, in THIS worker: the real
#             engines on the request path are exercised (async DB checkout,
#             async Redis round-trip, a REAL DLP scan through the configured
#             engine that must find the planted PII), plus the in-flight
#             registry: any chat request that has outlived the hard timeout
#             is "stuck" and turns readiness red. The whole probe runs under
#             ``readiness_timeout_seconds`` so it cannot itself hang — a
#             probe that wedges is worse than none. Worker-local: gunicorn
#             routes each probe to one of N workers, so a monitor sees a
#             wedged worker on ~1/N of probes; ``worker_pid`` is reported
#             so the pattern is legible.
#
# Check functions are module attributes so tests can monkeypatch them.

_PLANTED_PII = "probe: call 081-234-5678 or mail probe@example.com"


async def _ready_check_db() -> str:
    from app.core.db import get_async_engine

    engine_async = get_async_engine()
    async with engine_async.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return "ok"


async def _ready_check_redis() -> str:
    from app.services._redis_pool import get_async_redis

    r = get_async_redis()
    pong = await r.ping()
    return "ok" if pong else "no-pong"


async def _ready_check_dlp() -> str:
    """A real scan through the engine the request path uses — fail-over included.

    FIX103 Part A: with DLP_ENGINE=rust, a sidecar that is stopped or wedged
    does NOT let text through unmasked. ``orchestrator._run_dlp_scan`` gets
    ``None`` from the Rust client and rescans on the Python registry (31
    detectors, a superset of the sidecar's 30); proven live with the sidecar
    stopped and paused — the provider answered "cannot see" for the planted
    phone and email. So a sidecar outage is not "a chat request cannot be
    masked", and readiness no longer says it is: it reports ``degraded`` and
    stays 200. It goes red only when NO engine can scan the planted PII.

    Before this, /ready returned 503 for a sidecar outage while every request
    was still being masked, and the backend container's healthcheck (which
    probes /ready) would have called a working backend unhealthy.
    """
    from app.services.dlp.detectors.registry import get_default_registry

    def _python_found() -> set:
        return {m.detector_name for m in get_default_registry(ner_enabled=False).scan_text(_PLANTED_PII)}

    engine_name = str(getattr(settings, "dlp_engine", "python")).lower()
    if engine_name in ("rust", "shadow"):
        from app.services.dlp.rust_dlp_client import RustDlpClient

        res = await RustDlpClient.instance().scan_text(_PLANTED_PII, ner_enabled=False)
        if res is not None and {m.detector_name for m in res.matches} & {"phone", "email"}:
            return f"ok ({engine_name})"
        why = "unreachable" if res is None else "found nothing"
        if not (await asyncio.to_thread(_python_found)) & {"phone", "email"}:
            raise RuntimeError(f"no dlp engine can scan: rust sidecar {why}, python fallback found nothing")
        return f"degraded: rust sidecar {why} — masking on the python fallback"
    if not (await asyncio.to_thread(_python_found)) & {"phone", "email"}:
        raise RuntimeError(f"dlp engine scanned but found nothing ({engine_name})")
    return f"ok ({engine_name})"


async def _ready_loop_lag_ms() -> float:
    t0 = time.perf_counter()
    await asyncio.sleep(0)
    return round((time.perf_counter() - t0) * 1000, 2)


@router.get("/live")
async def live():
    """Liveness: the process is alive and its event loop turns."""
    import os

    return {"status": "alive", "worker_pid": os.getpid()}


def _stuck_after_s() -> float:
    """A chat request older than this is stuck (FIX26: hard timeout + 30 s)."""
    return float(getattr(settings, "request_hard_timeout_seconds", 120.0)) + 30.0


def worker_record() -> dict:
    """FIX131 — what this worker publishes on the worker board every few seconds.

    /ready on ANY worker of the container reads these (app/core/worker_board.py):
    this worker's stuck requests, its last turn, whether it leads.
    """
    from app.core import leader, turn_pulse
    from app.core.inflight import registry

    snap = registry().snapshot(stuck_after_s=_stuck_after_s())
    return {
        "inflight": {"count": snap.count, "oldest_age_s": snap.oldest_age_s, "stuck": snap.stuck},
        "leader": leader.is_leader(),
        **turn_pulse.timestamps(),
    }


@router.get("/ready")
async def ready():
    """Readiness: a chat request could complete now, in this worker.

    FIX131 — the engine checks are this worker's; ``stuck_requests``,
    ``turns`` and ``container`` cover every worker of the container (the
    worker board), so the answer does not depend on which worker the probe
    landed on.
    """
    import os

    from app.core import leader, worker_board
    from app.core.inflight import registry

    budget = float(getattr(settings, "readiness_timeout_seconds", 3.0))
    stuck_after = _stuck_after_s()
    checks: dict = {"worker_pid": os.getpid()}
    failures: list[str] = []

    async def _run(name: str, coro):
        try:
            checks[name] = await coro
        except Exception as exc:  # noqa: BLE001 — every failure is a reason
            checks[name] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
            failures.append(name)

    async def _all():
        checks["event_loop_lag_ms"] = await _ready_loop_lag_ms()
        await asyncio.gather(
            _run("database", _ready_check_db()),
            _run("redis", _ready_check_redis()),
            _run("dlp_engine", _ready_check_dlp()),
        )

    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(_all(), timeout=budget)
    except asyncio.TimeoutError:
        failures.append("probe_timeout")
        checks["probe"] = f"timed out after {budget:.1f}s"
    checks["probe_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    snap = registry().snapshot(stuck_after_s=stuck_after)
    checks["inflight"] = {
        "count": snap.count,
        "oldest_age_s": snap.oldest_age_s,
        "stuck": snap.stuck,
        "limit": snap.limit,
        "available": snap.available,
    }
    peers = worker_board.peers()
    peer_stuck = sum(int((p.get("inflight") or {}).get("stuck") or 0) for p in peers)
    leader_pid = os.getpid() if leader.is_leader() else next(
        (p.get("pid") for p in peers if p.get("leader")), None
    )
    checks["container"] = {
        "workers": 1 + len(peers),
        "inflight": snap.count + sum(int((p.get("inflight") or {}).get("count") or 0) for p in peers),
        "stuck": snap.stuck + peer_stuck,
        "leader_pid": leader_pid,
    }
    if snap.stuck or peer_stuck:
        failures.append("stuck_requests")

    # FIX129 A3 — the 2026-09-22 wedge was one transaction idle in
    # transaction holding a row lock; the engines answered, so every
    # engine check above stayed green. The pg_stat_activity sampler
    # (services/pg_activity.py) keeps its last sample in memory; a
    # transaction idle or lock-blocked past ``pg_stuck_transaction_seconds``
    # turns readiness red, and the last completed turn's age is reported
    # so "ready" can be read against "actually answering".
    # FIX131: every worker runs its own sampler of the same database
    # (main.py), so whichever worker answers, this reflects the database
    # within pg_activity_sample_seconds.
    from app.core import turn_pulse
    from app.services import pg_activity

    stuck_tx_after = float(getattr(settings, "pg_stuck_transaction_seconds", 60.0))
    sample = pg_activity.last_sample()
    checks["pg_activity"] = sample.as_dict() if sample is not None else {"sampled": False}
    # A sample the sampler stopped refreshing (task dead, DB unreachable —
    # the engine check above reports that) must not hold readiness red.
    sample_fresh = sample is not None and (time.time() - sample.at) <= 8 * float(
        getattr(settings, "pg_activity_sample_seconds", 15.0)
    )
    if sample_fresh and sample.stuck_age_s >= stuck_tx_after:
        failures.append("stuck_transaction")
    checks["turns"] = turn_pulse.snapshot(peers)

    status = "ready" if not failures else "not-ready"
    # FIX103 — ready but running on a fallback is said, not hidden in a string.
    degraded = [k for k, v in checks.items() if isinstance(v, str) and v.startswith("degraded")]
    body = {"status": status, "failures": failures, "degraded": degraded, "checks": checks}
    return JSONResponse(status_code=200 if not failures else 503, content=body)


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

    # Redis check — short timeout so it doesn't block workers.
    # FIX49 Part C — built from settings.redis_url, the SAME config line
    # the real pool (services/_redis_pool.py) and /ready use. The old
    # host+port construction carried no password, so with --requirepass
    # on (docker-compose.yml) this check failed on AUTH forever and the
    # whole service reported "degraded" while /ready said redis: ok —
    # a permanently-lying health signal that trains people to ignore it.
    try:
        import redis
        r = redis.Redis.from_url(
            settings.redis_url,
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

    # FIX38 A7 — the Thai NER layer lives in the ner-service. When the
    # layer is on and the service (or its Thai tagger) is not, the gateway
    # is masking fewer names than it claims: degraded, and a WARNING so it
    # shows up somewhere other than a leak.
    ner = _probe_ner_service()
    checks["ner_service"] = ner
    thai_ner_down = bool(ner.get("thai_ner_layer")) and (
        ner.get("status") != "healthy" or ner.get("th_ner") == "not_ready"
    )
    if thai_ner_down:
        _hlog.warning("thai_ner_service_down", **{k: v for k, v in ner.items() if k != "model"})

    db_ok = checks["database"] == "ok"
    if not db_ok:
        overall = "unhealthy"
    elif checks["redis"] != "ok" or any_active_sidecar_down or thai_ner_down:
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "service": "secure-ai-gateway",
        "version": "0.1.0",
        "checks": checks,
    }
