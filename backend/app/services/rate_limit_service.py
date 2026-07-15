"""Redis-backed sliding window rate limiter.

Three independent scopes are enforced per request:
- per API key    (default 60  req/min)
- per project    (default 300 req/min)
- per tenant     (default 1000 req/min)

Fail-open: if Redis is unreachable, requests are allowed through rather than
blocked. Rate limiting is a safety net, not an auth gate.

May 2026 scalability fix: the Redis transport is now ``redis.asyncio``
with a shared connection pool. The async API
(``check_all_async`` / ``_sliding_window_async``) is the new
preferred entry point — the orchestrator awaits it. The original
sync names are kept as legacy wrappers (via ``run_sync``) so test
fakes that inject a sync-only Redis double continue to work.
"""

from __future__ import annotations

import inspect
import os
import time
from collections import defaultdict
from typing import Any

from app.observability.metrics import (
    RATE_LIMIT_CONSERVATIVE_DENY_TOTAL,
    RATE_LIMIT_FAIL_OPEN_TOTAL,
    RATE_LIMIT_LUA_FALLBACK_TOTAL,
    SIDECAR_FALLBACK_TOTAL,
    SIDECAR_REQUEST_TOTAL,
)
from app.services._redis_pool import get_async_redis, run_sync


# Fallback ceilings used when settings can't be read (import-time edge
# cases, tests that bypass settings). The LIVE values come from the
# env-overridable ``settings.rate_limit_*_per_minute`` via the resolver
# helpers below, so an operator can retune without a code change.
DEFAULT_KEY_LIMIT = 60
DEFAULT_PROJECT_LIMIT = 3000
DEFAULT_TENANT_LIMIT = 10000
DEFAULT_WINDOW_SECONDS = 60


def _limit_from_settings(attr: str, fallback: int) -> int:
    """Read a positive int rate-limit ceiling from settings.

    Resolved on every call (not cached) so a test that monkeypatches the
    setting — or a config reload — takes effect immediately. Any failure
    (settings unavailable, non-int, non-positive) yields ``fallback``.
    """
    try:
        from app.core.config import settings

        value = int(getattr(settings, attr))
    except Exception:  # noqa: BLE001
        return fallback
    return value if value > 0 else fallback


def key_limit() -> int:
    return _limit_from_settings("rate_limit_key_per_minute", DEFAULT_KEY_LIMIT)


def project_limit_default() -> int:
    return _limit_from_settings(
        "rate_limit_project_per_minute", DEFAULT_PROJECT_LIMIT
    )


def tenant_limit_default() -> int:
    return _limit_from_settings(
        "rate_limit_tenant_per_minute", DEFAULT_TENANT_LIMIT
    )

# --- Conservative in-process limiter (Redis-outage fallback) ----------
# Used when Redis is unreachable, in two distinct ways (Phase 12 A4):
#   * GENERAL traffic limiters (this service): only when
#     settings.rate_limit_redis_outage_mode == "conservative"; default
#     stays fail-open (historical behaviour).
#   * CREDENTIAL-guarding limiters (admin login in
#     app/services/admin/login_service.py, portal password reset in
#     app/services/portal/password_reset_service.py): ALWAYS — they call
#     ``_conservative_allow`` directly on outage regardless of the
#     outage-mode setting, because failing open there would hand out a
#     free brute-force window on every Redis blip.
# Per-worker (not shared across workers/pods), best-effort: the goal is
# a cheap cap during an outage, not exact global enforcement.
_CONSERVATIVE_LIMIT = 10  # admits per window, per scope key
_CONSERVATIVE_WINDOW = 60  # seconds
_conservative_hits: dict[str, list[float]] = defaultdict(list)


def _conservative_allow(
    scope_key: str,
    limit: int = _CONSERVATIVE_LIMIT,
    window_sec: int = _CONSERVATIVE_WINDOW,
) -> bool:
    """Per-worker sliding-window admit check for the Redis-outage path.

    ``limit`` / ``window_sec`` default to the general-traffic values;
    credential-guarding callers pass their own ceilings (e.g. the
    password-reset 3/hour cap).
    """
    now = time.monotonic()
    cutoff = now - window_sec
    hits = _conservative_hits[scope_key]
    hits[:] = [t for t in hits if t > cutoff]
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def _outage_result(scope_key: str, limit: int, now: float, window_sec: int) -> dict:
    """Limiter response when the Redis backend is unreachable.

    ``fail_open`` (default) admits unlimited and increments the fail-open
    counter. ``conservative`` applies the in-process per-key cap and
    increments the deny counter on a rejection. Either way the outage is
    metered, never silent.
    """
    from app.core.config import settings

    if settings.rate_limit_redis_outage_mode == "conservative":
        allowed = _conservative_allow(scope_key)
        if not allowed:
            RATE_LIMIT_CONSERVATIVE_DENY_TOTAL.inc()
        return {
            "allowed": allowed,
            "remaining": max(0, _CONSERVATIVE_LIMIT - len(_conservative_hits[scope_key])),
            "reset_at": int(now + window_sec),
            "limit": _CONSERVATIVE_LIMIT,
        }

    RATE_LIMIT_FAIL_OPEN_TOTAL.inc()
    return {
        "allowed": True,
        "remaining": limit,
        "reset_at": int(now + window_sec),
        "limit": limit,
    }


# Perf-test-only override knobs. Setting
# ``PERF_TEST_PROJECT_LIMIT=120000`` in the environment raises the
# per-project ceiling for the project identified by
# ``PERF_TEST_PROJECT_ID`` (default ``perf-test-project``).
_PERF_PROJECT_ID = os.environ.get("PERF_TEST_PROJECT_ID", "perf-test-project")
_PERF_TENANT_ID = os.environ.get("PERF_TEST_TENANT_ID", "perf-test-tenant")


def _env_int(key: str) -> int | None:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


def _perf_project_limit_override() -> int | None:
    return _env_int("PERF_TEST_PROJECT_LIMIT")


def _perf_tenant_limit_override() -> int | None:
    return _env_int("PERF_TEST_TENANT_LIMIT")


# Atomic all-or-nothing sliding-window check across the 3 scopes, in a
# single Redis EVAL. Fixes the over-admission race: the legacy path ran
# 3 sequential pipelines and decided allow/deny CLIENT-side AFTER an
# unconditional ZADD, so concurrent requests near the boundary could
# read a stale count and all be admitted. Here the decision is computed
# from the counts BEFORE any ZADD, and ZADD happens only when allowed —
# atomic because Redis runs the script single-threaded. KEYS (not
# hard-coded names) keep it Redis-Cluster-compatible.
#   KEYS[1..3] = rl:key, rl:proj, rl:tenant
#   ARGV       = now, cutoff, key_limit, proj_limit, tenant_limit, expire, member
#   returns    = {denied(0|1|2|3), c1, c2, c3, rem1, rem2, rem3}
_CHECK_ALL_LUA = """
local cutoff = ARGV[2]
local l1 = tonumber(ARGV[3]); local l2 = tonumber(ARGV[4]); local l3 = tonumber(ARGV[5])
local expire = tonumber(ARGV[6]); local member = ARGV[7]
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff); local c1 = redis.call('ZCARD', KEYS[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, cutoff); local c2 = redis.call('ZCARD', KEYS[2])
redis.call('ZREMRANGEBYSCORE', KEYS[3], 0, cutoff); local c3 = redis.call('ZCARD', KEYS[3])
local denied = 0
if c1 >= l1 then denied = 1 elseif c2 >= l2 then denied = 2 elseif c3 >= l3 then denied = 3 end
if denied == 0 then
  redis.call('ZADD', KEYS[1], ARGV[1], member); redis.call('EXPIRE', KEYS[1], expire)
  redis.call('ZADD', KEYS[2], ARGV[1], member); redis.call('EXPIRE', KEYS[2], expire)
  redis.call('ZADD', KEYS[3], ARGV[1], member); redis.call('EXPIRE', KEYS[3], expire)
end
local add = 0; if denied == 0 then add = 1 end
local r1 = l1 - c1 - add; if r1 < 0 then r1 = 0 end
local r2 = l2 - c2 - add; if r2 < 0 then r2 = 0 end
local r3 = l3 - c3 - add; if r3 < 0 then r3 = 0 end
return {denied, c1, c2, c3, r1, r2, r3}
"""


class RateLimitService:
    """Sliding-window rate limiter keyed on API key / project / tenant."""

    def __init__(self, redis_client: Any = None) -> None:
        self._fake_client = redis_client
        self._async_client: Any | None = None

    def _redis(self) -> Any:
        if self._fake_client is not None:
            return self._fake_client
        if self._async_client is None:
            self._async_client = get_async_redis()
        return self._async_client

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def _sliding_window_async(
        self,
        key: str,
        limit: int,
        window_sec: int = DEFAULT_WINDOW_SECONDS,
    ) -> dict:
        now = time.time()
        cutoff = now - window_sec

        try:
            client = self._redis()
            pipe = client.pipeline(transaction=False)
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            pipe.zadd(key, {f"{now:.6f}:{time.perf_counter_ns()}": now})
            pipe.expire(key, window_sec + 1)
            exec_result = pipe.execute()
            if inspect.isawaitable(exec_result):
                exec_result = await exec_result
            _, count_before, _, _ = exec_result
        except Exception:
            # Redis unreachable. Default = fail open (admit unlimited; the
            # limiter is a safety net, not an auth gate). When
            # RATE_LIMIT_REDIS_OUTAGE_MODE=conservative, apply a small
            # in-process per-key cap instead. Both branches are metered
            # (Audit H6 — a Redis outage must be alertable, not silent).
            return _outage_result(key, limit, now, window_sec)

        allowed = count_before < limit
        remaining = max(0, limit - count_before - 1)
        return {
            "allowed": allowed,
            "remaining": remaining,
            "reset_at": int(now + window_sec),
            "limit": limit,
        }

    async def check_key_async(
        self, key_id: str, limit: int = DEFAULT_KEY_LIMIT
    ) -> dict:
        return await self._sliding_window_async(f"rl:key:{key_id}", limit)

    async def check_project_async(
        self, project_id: str, limit: int = DEFAULT_PROJECT_LIMIT
    ) -> dict:
        effective = limit
        if project_id == _PERF_PROJECT_ID:
            override = _perf_project_limit_override()
            if override is not None:
                effective = override
        return await self._sliding_window_async(f"rl:proj:{project_id}", effective)

    async def check_tenant_async(
        self, tenant_id: str, limit: int = DEFAULT_TENANT_LIMIT
    ) -> dict:
        effective = limit
        if tenant_id == _PERF_TENANT_ID:
            override = _perf_tenant_limit_override()
            if override is not None:
                effective = override
        return await self._sliding_window_async(f"rl:tenant:{tenant_id}", effective)

    async def check_all_async(
        self,
        key_id: str,
        project_id: str,
        tenant_id: str,
    ) -> dict:
        # Phase-3b: when the operator flips ``RATE_LIMITER_ENGINE``
        # to ``rust``, hand off to the Rust sidecar. Any failure
        # (timeout, RpcError, sidecar unreachable, grpcio import
        # error) falls through to the legacy Python pipeline below
        # — same fail-open contract the limiter has always had.
        from app.core.config import settings

        if settings.rate_limiter_engine == "rust":
            try:
                from app.services.rate_limiter_client import (
                    RateLimiterClient,
                    RateLimiterUnavailable,
                )
            except Exception:  # noqa: BLE001
                SIDECAR_FALLBACK_TOTAL.labels(
                    engine="rate_limiter", reason="unavailable"
                ).inc()
                SIDECAR_REQUEST_TOTAL.labels(
                    engine="rate_limiter", status="fallback"
                ).inc()
                return await self._check_all_atomic(key_id, project_id, tenant_id)

            try:
                client = RateLimiterClient.instance()
            except RateLimiterUnavailable:
                SIDECAR_FALLBACK_TOTAL.labels(
                    engine="rate_limiter", reason="unavailable"
                ).inc()
                SIDECAR_REQUEST_TOTAL.labels(
                    engine="rate_limiter", status="fallback"
                ).inc()
                return await self._check_all_atomic(key_id, project_id, tenant_id)

            try:
                # Per-project / per-tenant overrides — the Rust
                # service receives the same effective limits the
                # Python path would compute (perf-test overrides
                # included).
                project_limit = project_limit_default()
                if project_id == _PERF_PROJECT_ID:
                    override = _perf_project_limit_override()
                    if override is not None:
                        project_limit = override
                tenant_limit = tenant_limit_default()
                if tenant_id == _PERF_TENANT_ID:
                    override = _perf_tenant_limit_override()
                    if override is not None:
                        tenant_limit = override
                result = await client.check_all(
                    key_id=key_id,
                    project_id=project_id,
                    tenant_id=tenant_id,
                    key_limit=key_limit(),
                    project_limit=project_limit,
                    tenant_limit=tenant_limit,
                    window_seconds=DEFAULT_WINDOW_SECONDS,
                )
                SIDECAR_REQUEST_TOTAL.labels(
                    engine="rate_limiter", status="success"
                ).inc()
                return result
            except Exception:  # noqa: BLE001
                # RPC error / timeout — fall back to the local engine
                # so a sidecar incident cannot block the request path.
                SIDECAR_FALLBACK_TOTAL.labels(
                    engine="rate_limiter", reason="error"
                ).inc()
                SIDECAR_REQUEST_TOTAL.labels(
                    engine="rate_limiter", status="fallback"
                ).inc()
                return await self._check_all_atomic(key_id, project_id, tenant_id)

        return await self._check_all_atomic(key_id, project_id, tenant_id)

    async def check_window_async(
        self,
        scope_key: str,
        limit: int,
        window_sec: int,
    ) -> dict:
        """Sliding-window check on an ARBITRARY scope key + window length.

        The per-key/project/tenant helpers above are hard-wired to the
        60s request window. Cost-capped endpoints (image generation) need
        hour / day windows instead, so they call this directly with their
        own ``rl:img:...`` scope key. Reuses the exact same Redis ZSET
        backend + fail-open contract — no separate counter store, and no
        Rust-sidecar involvement (the sidecar only owns the 3-scope
        per-minute request limiter).
        """
        return await self._sliding_window_async(scope_key, limit, window_sec)

    async def _check_all_python(
        self,
        key_id: str,
        project_id: str,
        tenant_id: str,
    ) -> dict:
        """Legacy in-process pipeline — three sequential ZSET checks.

        Pulled out of ``check_all_async`` so the Rust engine branch
        can call back here on any failure without duplicating logic.
        """
        key_result = await self.check_key_async(key_id, key_limit())
        if not key_result["allowed"]:
            return {**key_result, "scope": "api_key"}

        project_result = await self.check_project_async(
            project_id, project_limit_default()
        )
        if not project_result["allowed"]:
            return {**project_result, "scope": "project"}

        tenant_result = await self.check_tenant_async(
            tenant_id, tenant_limit_default()
        )
        if not tenant_result["allowed"]:
            return {**tenant_result, "scope": "tenant"}

        return {
            "allowed": True,
            "scope": None,
            "remaining": key_result["remaining"],
            "reset_at": key_result["reset_at"],
            "limit": key_result["limit"],
        }

    async def _check_all_atomic(
        self,
        key_id: str,
        project_id: str,
        tenant_id: str,
    ) -> dict:
        """Atomic 3-scope check via the Lua EVAL; falls back to the
        sequential pipeline if EVAL is unavailable (e.g. a fakeredis
        double without Lua) or errors. Fail-open preserved either way."""
        try:
            return await self._check_all_lua(key_id, project_id, tenant_id)
        except Exception:  # noqa: BLE001
            # Audit H8: the atomic Lua path errored → we drop to the
            # NON-atomic sequential path (over-admission race possible).
            # Count it so this silent correctness degradation is visible.
            RATE_LIMIT_LUA_FALLBACK_TOTAL.inc()
            return await self._check_all_python(key_id, project_id, tenant_id)

    async def _check_all_lua(
        self,
        key_id: str,
        project_id: str,
        tenant_id: str,
    ) -> dict:
        """All-or-nothing sliding-window check across the 3 scopes in a
        single Redis EVAL — no over-admission race (decision computed
        from counts BEFORE any ZADD; ZADD only when allowed)."""
        now = time.time()
        cutoff = now - DEFAULT_WINDOW_SECONDS
        member = f"{now:.6f}:{time.perf_counter_ns()}"

        key_lim = key_limit()
        project_limit = project_limit_default()
        if project_id == _PERF_PROJECT_ID:
            override = _perf_project_limit_override()
            if override is not None:
                project_limit = override
        tenant_limit = tenant_limit_default()
        if tenant_id == _PERF_TENANT_ID:
            override = _perf_tenant_limit_override()
            if override is not None:
                tenant_limit = override

        keys = [
            f"rl:key:{key_id}",
            f"rl:proj:{project_id}",
            f"rl:tenant:{tenant_id}",
        ]
        args = [
            f"{now:.6f}",
            f"{cutoff:.6f}",
            key_lim,
            project_limit,
            tenant_limit,
            DEFAULT_WINDOW_SECONDS + 1,
            member,
        ]

        client = self._redis()
        # redis caches the script by SHA after the first EVAL, so a plain
        # EVAL gets EVALSHA performance server-side without us tracking the
        # digest. Works on the async client and on a fakeredis double that
        # implements Lua.
        res = client.eval(_CHECK_ALL_LUA, len(keys), *keys, *args)
        if inspect.isawaitable(res):
            res = await res

        denied = int(res[0])
        reset_at = int(now + DEFAULT_WINDOW_SECONDS)
        limits = [key_lim, project_limit, tenant_limit]
        scopes = ["api_key", "project", "tenant"]
        if denied == 0:
            return {
                "allowed": True,
                "scope": None,
                "remaining": int(res[4]),
                "reset_at": reset_at,
                "limit": key_lim,
            }
        idx = denied - 1
        return {
            "allowed": False,
            "scope": scopes[idx],
            "remaining": int(res[4 + idx]),
            "reset_at": reset_at,
            "limit": limits[idx],
        }

    # ------------------------------------------------------------------
    # Sync compat — legacy callers + 25 test files keep working
    # ------------------------------------------------------------------

    def _sliding_window(
        self,
        key: str,
        limit: int,
        window_sec: int = DEFAULT_WINDOW_SECONDS,
    ) -> dict:
        if self._fake_client is not None:
            return self._sliding_window_via_fake(key, limit, window_sec)
        return run_sync(self._sliding_window_async(key, limit, window_sec))

    def check_key(self, key_id: str, limit: int = DEFAULT_KEY_LIMIT) -> dict:
        return self._sliding_window(f"rl:key:{key_id}", limit)

    def check_project(
        self, project_id: str, limit: int = DEFAULT_PROJECT_LIMIT
    ) -> dict:
        effective = limit
        if project_id == _PERF_PROJECT_ID:
            override = _perf_project_limit_override()
            if override is not None:
                effective = override
        return self._sliding_window(f"rl:proj:{project_id}", effective)

    def check_tenant(
        self, tenant_id: str, limit: int = DEFAULT_TENANT_LIMIT
    ) -> dict:
        effective = limit
        if tenant_id == _PERF_TENANT_ID:
            override = _perf_tenant_limit_override()
            if override is not None:
                effective = override
        return self._sliding_window(f"rl:tenant:{tenant_id}", effective)

    def check_all(
        self,
        key_id: str,
        project_id: str,
        tenant_id: str,
    ) -> dict:
        key_result = self.check_key(key_id, key_limit())
        if not key_result["allowed"]:
            return {**key_result, "scope": "api_key"}

        project_result = self.check_project(project_id, project_limit_default())
        if not project_result["allowed"]:
            return {**project_result, "scope": "project"}

        tenant_result = self.check_tenant(tenant_id, tenant_limit_default())
        if not tenant_result["allowed"]:
            return {**tenant_result, "scope": "tenant"}

        return {
            "allowed": True,
            "scope": None,
            "remaining": key_result["remaining"],
            "reset_at": key_result["reset_at"],
            "limit": key_result["limit"],
        }

    # ------------------------------------------------------------------
    # Fake-client path — preserved verbatim from the pre-async version
    # so injected sync fakes work without rewriting.
    # ------------------------------------------------------------------

    def _sliding_window_via_fake(
        self,
        key: str,
        limit: int,
        window_sec: int,
    ) -> dict:
        now = time.time()
        cutoff = now - window_sec
        try:
            pipe = self._fake_client.pipeline(transaction=False)
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            pipe.zadd(key, {f"{now:.6f}:{time.perf_counter_ns()}": now})
            pipe.expire(key, window_sec + 1)
            _, count_before, _, _ = pipe.execute()
        except Exception:
            return _outage_result(key, limit, now, window_sec)
        allowed = count_before < limit
        remaining = max(0, limit - count_before - 1)
        return {
            "allowed": allowed,
            "remaining": remaining,
            "reset_at": int(now + window_sec),
            "limit": limit,
        }
