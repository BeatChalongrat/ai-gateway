"""Response cache — exact-match on (model + messages) hash, stored in Redis.

Caches successful non-streaming chat completions so identical follow-up
requests skip the provider call. Failures are non-fatal: a cache miss or a
Redis outage just means we fall through to the provider as normal.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis as redis_lib

from app.core.config import settings


DEFAULT_TTL_SECONDS = 3600


class CacheService:
    """Semantic (exact-match) response cache keyed on (model, messages)."""

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
        self.default_ttl = DEFAULT_TTL_SECONDS

    def _key(self, model: str, messages: list, project_id: str) -> str:
        payload = json.dumps(
            {"model": model, "messages": messages},
            sort_keys=True,
            ensure_ascii=False,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return f"cache:{project_id}:{digest}"

    def get(
        self,
        model: str,
        messages: list,
        project_id: str,
    ) -> dict | None:
        try:
            raw = self._redis.get(self._key(model, messages, project_id))
            if raw is None:
                self._incr_counter(f"cache_misses:{project_id}")
                return None
            self._incr_counter(f"cache_hits:{project_id}")
            return json.loads(raw)
        except Exception:
            return None

    def set(
        self,
        model: str,
        messages: list,
        project_id: str,
        response: dict,
        ttl: int | None = None,
    ) -> None:
        try:
            key = self._key(model, messages, project_id)
            self._redis.setex(
                key,
                ttl or self.default_ttl,
                json.dumps(response, ensure_ascii=False),
            )
        except Exception:
            pass  # non-fatal

    def get_stats(self, project_id: str) -> dict:
        try:
            hits = int(self._redis.get(f"cache_hits:{project_id}") or 0)
            misses = int(self._redis.get(f"cache_misses:{project_id}") or 0)
        except Exception:
            hits, misses = 0, 0
        total = hits + misses
        return {
            "cache_hits": hits,
            "cache_misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }

    def _incr_counter(self, key: str) -> None:
        try:
            self._redis.incr(key)
        except Exception:
            pass
