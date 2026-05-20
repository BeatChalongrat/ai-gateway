"""Response cache — exact-match on (model + messages) hash, stored in Redis.

May 2026 scalability fix: the underlying Redis transport is now
``redis.asyncio``. The class exposes ``async`` methods for new callers
(the orchestrator) and keeps the legacy sync method names as thin
``run_sync`` wrappers so the existing test fleet and any non-async
caller continue to work without immediate edits.

Failure model is unchanged: failures are non-fatal — a cache miss or
a Redis outage just means we fall through to the provider as normal.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services._redis_pool import get_async_redis, run_sync


DEFAULT_TTL_SECONDS = 3600


class CacheService:
    """Semantic (exact-match) response cache keyed on (model, messages)."""

    def __init__(self, redis_client: Any = None) -> None:
        # ``redis_client`` is preserved for tests that inject a fake
        # client. Tests pass an object with sync ``get`` / ``setex`` /
        # ``incr`` methods; we route the sync API around it. The
        # async API only kicks in when no override is supplied, since
        # the fake clients aren't async-aware.
        self._fake_client = redis_client
        # The real asyncio client is created lazily by ``_redis()`` so
        # importing this module never opens a Redis connection — keeps
        # the test suite cheap.
        self._async_client: Any | None = None
        self.default_ttl = DEFAULT_TTL_SECONDS

    def _redis(self) -> Any:
        if self._fake_client is not None:
            return self._fake_client
        if self._async_client is None:
            self._async_client = get_async_redis()
        return self._async_client

    def _key(
        self,
        model: str,
        messages: list,
        project_id: str,
        api_key_id: str | None = None,
    ) -> str:
        # When ``api_key_id`` is supplied (the orchestrator passes it for
        # DLP RESTORE profiles), the key is scoped per-caller so a
        # detector miss that leaks raw PII into the masked response can
        # never be served to a *different* user hitting the same prompt
        # template. Non-DLP requests continue to pass ``None`` and share
        # the per-project cache so common queries stay deduped across
        # users. ``api_key_id`` is mixed into BOTH the namespace and the
        # hashed payload to defend against the digest colliding with a
        # parallel-namespace entry built without an api_key_id.
        payload_obj: dict[str, Any] = {"model": model, "messages": messages}
        if api_key_id:
            payload_obj["api_key_id"] = api_key_id
        payload = json.dumps(payload_obj, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        if api_key_id:
            return f"cache:{project_id}:{api_key_id}:{digest}"
        return f"cache:{project_id}:{digest}"

    # ------------------------------------------------------------------
    # Async API — preferred for new callers
    # ------------------------------------------------------------------

    async def get_async(
        self,
        model: str,
        messages: list,
        project_id: str,
        api_key_id: str | None = None,
    ) -> dict | None:
        try:
            client = self._redis()
            raw = await _maybe_await(
                client.get(self._key(model, messages, project_id, api_key_id))
            )
            if raw is None:
                await _maybe_await(client.incr(f"cache_misses:{project_id}"))
                return None
            await _maybe_await(client.incr(f"cache_hits:{project_id}"))
            return json.loads(raw)
        except Exception:
            return None

    async def set_async(
        self,
        model: str,
        messages: list,
        project_id: str,
        response: dict,
        ttl: int | None = None,
        api_key_id: str | None = None,
    ) -> None:
        try:
            client = self._redis()
            await _maybe_await(
                client.setex(
                    self._key(model, messages, project_id, api_key_id),
                    ttl or self.default_ttl,
                    json.dumps(response, ensure_ascii=False),
                )
            )
        except Exception:
            pass  # non-fatal

    async def get_stats_async(self, project_id: str) -> dict:
        try:
            client = self._redis()
            hits_raw = await _maybe_await(client.get(f"cache_hits:{project_id}"))
            misses_raw = await _maybe_await(client.get(f"cache_misses:{project_id}"))
            hits = int(hits_raw or 0)
            misses = int(misses_raw or 0)
        except Exception:
            hits, misses = 0, 0
        total = hits + misses
        return {
            "cache_hits": hits,
            "cache_misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }

    # ------------------------------------------------------------------
    # Sync compat — kept so the 25-file test fleet and legacy callers
    # continue to work without immediate edits. New code should
    # ``await`` the matching ``_async`` method instead.
    # ------------------------------------------------------------------

    def get(
        self,
        model: str,
        messages: list,
        project_id: str,
        api_key_id: str | None = None,
    ) -> dict | None:
        if self._fake_client is not None:
            return self._get_via_fake(model, messages, project_id, api_key_id)
        return run_sync(self.get_async(model, messages, project_id, api_key_id))

    def set(
        self,
        model: str,
        messages: list,
        project_id: str,
        response: dict,
        ttl: int | None = None,
        api_key_id: str | None = None,
    ) -> None:
        if self._fake_client is not None:
            self._set_via_fake(model, messages, project_id, response, ttl, api_key_id)
            return
        run_sync(
            self.set_async(model, messages, project_id, response, ttl, api_key_id)
        )

    def get_stats(self, project_id: str) -> dict:
        if self._fake_client is not None:
            return self._get_stats_via_fake(project_id)
        return run_sync(self.get_stats_async(project_id))

    # ------------------------------------------------------------------
    # Fake-client fast paths — preserved verbatim from the pre-async
    # implementation so the existing fake-Redis tests don't need
    # rewrites.
    # ------------------------------------------------------------------

    def _get_via_fake(
        self,
        model: str,
        messages: list,
        project_id: str,
        api_key_id: str | None = None,
    ) -> dict | None:
        try:
            raw = self._fake_client.get(
                self._key(model, messages, project_id, api_key_id)
            )
            if raw is None:
                self._incr_fake(f"cache_misses:{project_id}")
                return None
            self._incr_fake(f"cache_hits:{project_id}")
            return json.loads(raw)
        except Exception:
            return None

    def _set_via_fake(
        self,
        model: str,
        messages: list,
        project_id: str,
        response: dict,
        ttl: int | None,
        api_key_id: str | None = None,
    ) -> None:
        try:
            self._fake_client.setex(
                self._key(model, messages, project_id, api_key_id),
                ttl or self.default_ttl,
                json.dumps(response, ensure_ascii=False),
            )
        except Exception:
            pass

    def _get_stats_via_fake(self, project_id: str) -> dict:
        try:
            hits = int(self._fake_client.get(f"cache_hits:{project_id}") or 0)
            misses = int(self._fake_client.get(f"cache_misses:{project_id}") or 0)
        except Exception:
            hits, misses = 0, 0
        total = hits + misses
        return {
            "cache_hits": hits,
            "cache_misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }

    def _incr_fake(self, key: str) -> None:
        try:
            self._fake_client.incr(key)
        except Exception:
            pass


async def _maybe_await(value: Any) -> Any:
    """Await *value* if it's a coroutine, return it unchanged otherwise.

    Lets the same code path serve both the async Redis client (whose
    methods return coroutines) and any future client that returns
    materialised values.
    """
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
