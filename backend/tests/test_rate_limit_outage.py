"""Rate-limiter behaviour when the Redis backend is unreachable.

Default mode is fail-open (admit unlimited). The opt-in "conservative"
mode applies a small in-process per-key cap instead. A fake Redis client
that raises on every call simulates the outage.
"""

import app.core.config as cfg
import pytest

from app.services.rate_limit_service import (
    _CONSERVATIVE_LIMIT,
    _conservative_hits,
    RateLimitService,
)


class _RaisingRedis:
    """Fake client standing in for a down Redis — every op raises."""

    def pipeline(self, *args, **kwargs):
        raise ConnectionError("redis down")


@pytest.fixture
def svc():
    return RateLimitService(redis_client=_RaisingRedis())


@pytest.fixture(autouse=True)
def _reset_conservative_state():
    _conservative_hits.clear()
    yield
    _conservative_hits.clear()


def test_fail_open_is_default(svc, monkeypatch):
    monkeypatch.setattr(cfg.settings, "rate_limit_redis_outage_mode", "fail_open")
    for _ in range(50):
        assert svc.check_key("k_failopen", limit=5)["allowed"] is True


def test_conservative_denies_over_limit(svc, monkeypatch):
    monkeypatch.setattr(cfg.settings, "rate_limit_redis_outage_mode", "conservative")
    verdicts = [
        svc.check_key("k_cons", limit=5)["allowed"]
        for _ in range(_CONSERVATIVE_LIMIT + 3)
    ]
    assert verdicts[:_CONSERVATIVE_LIMIT] == [True] * _CONSERVATIVE_LIMIT
    assert verdicts[_CONSERVATIVE_LIMIT] is False  # the (limit+1)th is denied


def test_conservative_is_per_key(svc, monkeypatch):
    monkeypatch.setattr(cfg.settings, "rate_limit_redis_outage_mode", "conservative")
    for _ in range(_CONSERVATIVE_LIMIT):
        svc.check_key("k_a", limit=5)
    assert svc.check_key("k_a", limit=5)["allowed"] is False  # exhausted
    assert svc.check_key("k_b", limit=5)["allowed"] is True   # independent
