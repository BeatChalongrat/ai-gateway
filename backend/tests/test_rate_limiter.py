"""Redis-backed rate limiter tests.

Uses an injected fake Redis client so tests don't require a running Redis.
The fake implements the ZSET ops the sliding window needs
(ZREMRANGEBYSCORE, ZCARD, ZADD, EXPIRE) and supports pipelining.
"""

from __future__ import annotations

import time

import pytest

from app.services.rate_limit_service import RateLimitService


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list = []

    def zremrangebyscore(self, key: str, minv, maxv):
        self._ops.append(("zremrangebyscore", key, minv, maxv))
        return self

    def zcard(self, key: str):
        self._ops.append(("zcard", key))
        return self

    def zadd(self, key: str, mapping: dict):
        self._ops.append(("zadd", key, mapping))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "zremrangebyscore":
                _, key, _min, _max = op
                zset = self._redis.store.get(key, {})
                to_remove = [m for m, s in zset.items() if s <= _max]
                for m in to_remove:
                    del zset[m]
                results.append(len(to_remove))
            elif op[0] == "zcard":
                _, key = op
                results.append(len(self._redis.store.get(key, {})))
            elif op[0] == "zadd":
                _, key, mapping = op
                zset = self._redis.store.setdefault(key, {})
                for member, score in mapping.items():
                    zset[member] = score
                results.append(len(mapping))
            elif op[0] == "expire":
                results.append(1)
        self._ops = []
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True):  # noqa: ARG002
        return _FakePipeline(self)


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def svc(fake_redis):
    return RateLimitService(redis_client=fake_redis)


class TestCheckKey:
    def test_under_limit_allowed(self, svc):
        for _ in range(5):
            r = svc.check_key("k1", limit=10)
            assert r["allowed"] is True
        assert r["remaining"] == 5

    def test_over_limit_blocked(self, svc):
        for _ in range(3):
            assert svc.check_key("k1", limit=3)["allowed"] is True
        blocked = svc.check_key("k1", limit=3)
        assert blocked["allowed"] is False
        assert blocked["remaining"] == 0
        assert "reset_at" in blocked

    def test_different_keys_independent(self, svc):
        assert svc.check_key("k1", limit=1)["allowed"] is True
        # k1 exhausted
        assert svc.check_key("k1", limit=1)["allowed"] is False
        # k2 untouched
        assert svc.check_key("k2", limit=1)["allowed"] is True


class TestCheckProject:
    def test_project_scope(self, svc):
        for _ in range(2):
            assert svc.check_project("proj1", limit=2)["allowed"] is True
        assert svc.check_project("proj1", limit=2)["allowed"] is False

    def test_project_and_key_independent(self, svc):
        # Separate Redis namespaces
        svc.check_key("k", limit=1)
        assert svc.check_project("k", limit=1)["allowed"] is True


class TestCheckTenant:
    def test_tenant_scope(self, svc):
        for _ in range(2):
            assert svc.check_tenant("t1", limit=2)["allowed"] is True
        assert svc.check_tenant("t1", limit=2)["allowed"] is False


class TestCheckAll:
    def test_all_allowed_returns_success(self, svc):
        r = svc.check_all("k1", "p1", "t1")
        assert r["allowed"] is True
        assert r["scope"] is None

    def test_reports_which_scope_failed(self, fake_redis):
        svc = RateLimitService(redis_client=fake_redis)
        # Monkey-patch: set per-scope limits low to force failure on key scope
        for _ in range(60):
            svc.check_key("k1")
        r = svc.check_all("k1", "p1", "t1")
        assert r["allowed"] is False
        assert r["scope"] == "api_key"


class TestRedisFailureFailsOpen:
    def test_redis_down_allows_request(self):
        class BrokenRedis:
            def pipeline(self, transaction: bool = True):  # noqa: ARG002
                raise ConnectionError("redis down")

        svc = RateLimitService(redis_client=BrokenRedis())
        r = svc.check_key("k", limit=1)
        assert r["allowed"] is True
