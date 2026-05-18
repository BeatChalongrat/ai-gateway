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
