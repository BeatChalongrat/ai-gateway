"""Response cache service tests (Redis-backed, uses injected fake client)."""

from __future__ import annotations

import pytest

from app.services.cache_service import CacheService


class _FakeRedis:
    """Minimal fake implementing the subset of redis-py used by CacheService."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    def get(self, key: str):
        return self.kv.get(key)

    def setex(self, key: str, ttl: int, value: str):  # noqa: ARG002
        self.kv[key] = value
        return True

    def incr(self, key: str):
        self.counters[key] = self.counters.get(key, 0) + 1
        self.kv[key] = str(self.counters[key])
        return self.counters[key]


@pytest.fixture
def cache():
    return CacheService(redis_client=_FakeRedis())


class TestResponseCache:
    def test_miss_returns_none(self, cache):
        result = cache.get("gpt-4o-mini", [{"role": "user", "content": "hi"}], "p1")
        assert result is None

    def test_round_trip(self, cache):
        messages = [{"role": "user", "content": "What is 2+2?"}]
        cache.set("gpt-4o-mini", messages, "p1", {"choices": [{"answer": "4"}]})
        hit = cache.get("gpt-4o-mini", messages, "p1")
        assert hit == {"choices": [{"answer": "4"}]}

    def test_key_isolation_by_project(self, cache):
        messages = [{"role": "user", "content": "same"}]
        cache.set("gpt-4o-mini", messages, "p1", {"tag": "project-1"})
        assert cache.get("gpt-4o-mini", messages, "p2") is None

    def test_key_isolation_by_model(self, cache):
        messages = [{"role": "user", "content": "same"}]
        cache.set("gpt-4o-mini", messages, "p1", {"tag": "mini"})
        assert cache.get("gpt-4o", messages, "p1") is None

    def test_key_isolation_by_messages(self, cache):
        cache.set(
            "gpt-4o-mini",
            [{"role": "user", "content": "a"}],
            "p1",
            {"tag": "first"},
        )
        assert (
            cache.get(
                "gpt-4o-mini",
                [{"role": "user", "content": "b"}],
                "p1",
            )
            is None
        )

    def test_message_order_matters(self, cache):
        cache.set(
            "gpt-4o-mini",
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ],
            "p1",
            {"tag": "original"},
        )
        reordered = [
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "first"},
        ]
        assert cache.get("gpt-4o-mini", reordered, "p1") is None


class TestStats:
    def test_hit_and_miss_counters(self, cache):
        messages = [{"role": "user", "content": "hi"}]
        cache.get("gpt-4o-mini", messages, "p1")  # miss
        cache.set("gpt-4o-mini", messages, "p1", {"ok": True})
        cache.get("gpt-4o-mini", messages, "p1")  # hit
        cache.get("gpt-4o-mini", messages, "p1")  # hit
        stats = cache.get_stats("p1")
        assert stats["cache_hits"] == 2
        assert stats["cache_misses"] == 1
        assert stats["hit_rate"] == round(2 / 3, 4)

    def test_empty_stats(self, cache):
        stats = cache.get_stats("unused")
        assert stats == {"cache_hits": 0, "cache_misses": 0, "hit_rate": 0.0}


class TestRedisFailureFailsOpen:
    def test_get_returns_none_on_redis_error(self):
        class BrokenRedis:
            def get(self, key):
                raise ConnectionError("down")

        cache = CacheService(redis_client=BrokenRedis())
        assert cache.get("m", [{"role": "user", "content": "x"}], "p") is None

    def test_set_swallows_redis_error(self):
        class BrokenRedis:
            def setex(self, *args, **kwargs):
                raise ConnectionError("down")

        cache = CacheService(redis_client=BrokenRedis())
        # Must not raise
        cache.set("m", [{"role": "user", "content": "x"}], "p", {"any": "thing"})
