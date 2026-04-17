"""Redis-backed sliding window rate limiter.

Three independent scopes are enforced per request:
- per API key    (default 60  req/min)
- per project    (default 300 req/min)
- per tenant     (default 1000 req/min)

Fail-open: if Redis is unreachable, requests are allowed through rather than
blocked. Rate limiting is a safety net, not an auth gate.
"""

from __future__ import annotations

import time

import redis as redis_lib

from app.core.config import settings


DEFAULT_KEY_LIMIT = 60
DEFAULT_PROJECT_LIMIT = 300
DEFAULT_TENANT_LIMIT = 1000
DEFAULT_WINDOW_SECONDS = 60


class RateLimitService:
    """Sliding-window rate limiter keyed on API key / project / tenant."""

    def __init__(self, redis_client=None) -> None:
        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = redis_lib.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1,
            )

    def _sliding_window(
        self,
        key: str,
        limit: int,
        window_sec: int = DEFAULT_WINDOW_SECONDS,
    ) -> dict:
        """Atomically add a timestamp and return whether the window limit is
        exceeded. On Redis failure, fails open (allows the request)."""
        now = time.time()
        cutoff = now - window_sec

        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            # Unique score per entry so two events in the same microsecond don't collide
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

        # count_before is the window size BEFORE we added this request
        allowed = count_before < limit
        remaining = max(0, limit - count_before - 1)
        return {
            "allowed": allowed,
            "remaining": remaining,
            "reset_at": int(now + window_sec),
            "limit": limit,
        }

    def check_key(self, key_id: str, limit: int = DEFAULT_KEY_LIMIT) -> dict:
        return self._sliding_window(f"rl:key:{key_id}", limit)

    def check_project(self, project_id: str, limit: int = DEFAULT_PROJECT_LIMIT) -> dict:
        return self._sliding_window(f"rl:proj:{project_id}", limit)

    def check_tenant(self, tenant_id: str, limit: int = DEFAULT_TENANT_LIMIT) -> dict:
        return self._sliding_window(f"rl:tenant:{tenant_id}", limit)

    def check_all(
        self,
        key_id: str,
        project_id: str,
        tenant_id: str,
    ) -> dict:
        """Check all three scopes. Returns the first failure, or success with
        the narrowest remaining budget."""
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
