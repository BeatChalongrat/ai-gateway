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
from typing import Any

from app.services._redis_pool import get_async_redis, run_sync


DEFAULT_KEY_LIMIT = 60
DEFAULT_PROJECT_LIMIT = 300
DEFAULT_TENANT_LIMIT = 1000
DEFAULT_WINDOW_SECONDS = 60

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
            return {
                "allowed": True,
                "remaining": limit,
                "reset_at": int(now + window_sec),
                "limit": limit,
            }

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
                return await self._check_all_atomic(key_id, project_id, tenant_id)

            try:
                client = RateLimiterClient.instance()
            except RateLimiterUnavailable:
                return await self._check_all_atomic(key_id, project_id, tenant_id)

            try:
                # Per-project / per-tenant overrides — the Rust
                # service receives the same effective limits the
                # Python path would compute (perf-test overrides
                # included).
                project_limit = DEFAULT_PROJECT_LIMIT
                if project_id == _PERF_PROJECT_ID:
                    override = _perf_project_limit_override()
                    if override is not None:
                        project_limit = override
                tenant_limit = DEFAULT_TENANT_LIMIT
                if tenant_id == _PERF_TENANT_ID:
                    override = _perf_tenant_limit_override()
                    if override is not None:
                        tenant_limit = override
                return await client.check_all(
                    key_id=key_id,
                    project_id=project_id,
                    tenant_id=tenant_id,
                    key_limit=DEFAULT_KEY_LIMIT,
                    project_limit=project_limit,
                    tenant_limit=tenant_limit,
                    window_seconds=DEFAULT_WINDOW_SECONDS,
                )
            except Exception:  # noqa: BLE001
                # RPC error / timeout — fall back to the local engine
                # so a sidecar incident cannot block the request path.
                return await self._check_all_atomic(key_id, project_id, tenant_id)

        return await self._check_all_atomic(key_id, project_id, tenant_id)

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
        key_result = await self.check_key_async(key_id)
        if not key_result["allowed"]:
            return {**key_result, "scope": "api_key"}

        project_result = await self.check_project_async(project_id)
        if not project_result["allowed"]:
            return {**project_result, "scope": "project"}

        tenant_result = await self.check_tenant_async(tenant_id)
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

        project_limit = DEFAULT_PROJECT_LIMIT
        if project_id == _PERF_PROJECT_ID:
            override = _perf_project_limit_override()
            if override is not None:
                project_limit = override
        tenant_limit = DEFAULT_TENANT_LIMIT
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
            DEFAULT_KEY_LIMIT,
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
        limits = [DEFAULT_KEY_LIMIT, project_limit, tenant_limit]
        scopes = ["api_key", "project", "tenant"]
        if denied == 0:
            return {
                "allowed": True,
                "scope": None,
                "remaining": int(res[4]),
                "reset_at": reset_at,
                "limit": DEFAULT_KEY_LIMIT,
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
        key_result = self.check_key(key_id)
        if not key_result["allowed"]:
            return {**key_result, "scope": "api_key"}

        project_result = self.check_project(project_id)
        if not project_result["allowed"]:
            return {**project_result, "scope": "project"}

        tenant_result = self.check_tenant(tenant_id)
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
            return {
                "allowed": True,
                "remaining": limit,
                "reset_at": int(now + window_sec),
                "limit": limit,
            }
        allowed = count_before < limit
        remaining = max(0, limit - count_before - 1)
        return {
            "allowed": allowed,
            "remaining": remaining,
            "reset_at": int(now + window_sec),
            "limit": limit,
        }
