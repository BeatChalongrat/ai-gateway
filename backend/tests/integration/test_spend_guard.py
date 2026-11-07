"""L1 session-budget spend guard — Sprint 2B.

Runs against a REAL Redis (DB 15) because the atomic reserve uses a Lua
``EVAL`` that fakeredis does not implement. Skips if Redis is unreachable.
"""

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.core.config import settings
from app.services.media_gen import spend_guard


@pytest_asyncio.fixture
async def rr():
    r = aioredis.from_url("redis://localhost:6379/15", decode_responses=True)
    try:
        await r.ping()
    except Exception:
        pytest.skip("dev Redis not reachable on localhost:6379")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


def _session_key():
    return spend_guard.SESSION_KEY.format(date=spend_guard._today())


@pytest.mark.asyncio
async def test_reserve_within_budget(rr, monkeypatch):
    monkeypatch.setattr(settings, "replicate_test_budget_usd", Decimal("5.00"))
    assert await spend_guard.check_and_reserve_session_budget(Decimal("1.00"), redis=rr) is True
    assert await spend_guard.get_session_spend(redis=rr) == Decimal("1.00")


@pytest.mark.asyncio
async def test_reserve_over_budget_rejected_no_mutation(rr, monkeypatch):
    monkeypatch.setattr(settings, "replicate_test_budget_usd", Decimal("5.00"))
    await spend_guard.check_and_reserve_session_budget(Decimal("4.50"), redis=rr)
    # 4.50 + 1.00 = 5.50 > 5.00 → refused, counter unchanged.
    assert await spend_guard.check_and_reserve_session_budget(Decimal("1.00"), redis=rr) is False
    assert await spend_guard.get_session_spend(redis=rr) == Decimal("4.50")
    # 4.50 + 0.40 = 4.90 ≤ 5.00 → allowed.
    assert await spend_guard.check_and_reserve_session_budget(Decimal("0.40"), redis=rr) is True
    assert await spend_guard.get_session_spend(redis=rr) == Decimal("4.90")


@pytest.mark.asyncio
async def test_release_refunds_and_clamps(rr, monkeypatch):
    monkeypatch.setattr(settings, "replicate_test_budget_usd", Decimal("5.00"))
    await spend_guard.check_and_reserve_session_budget(Decimal("4.90"), redis=rr)
    await spend_guard.release_session_budget(Decimal("0.40"), redis=rr)
    assert await spend_guard.get_session_spend(redis=rr) == Decimal("4.50")
    # Releasing more than the balance clamps at 0.
    await spend_guard.release_session_budget(Decimal("100"), redis=rr)
    assert await spend_guard.get_session_spend(redis=rr) == Decimal("0")


@pytest.mark.asyncio
async def test_counter_has_ttl(rr, monkeypatch):
    monkeypatch.setattr(settings, "replicate_test_budget_usd", Decimal("5.00"))
    await spend_guard.check_and_reserve_session_budget(Decimal("1.00"), redis=rr)
    ttl = await rr.ttl(_session_key())
    assert 0 < ttl <= 86400


@pytest.mark.asyncio
async def test_get_session_spend_empty(rr):
    assert await spend_guard.get_session_spend("2000-01-01", redis=rr) == Decimal("0")


@pytest.mark.asyncio
async def test_concurrent_reserve_is_atomic(rr, monkeypatch):
    # Budget $5.00; ten parallel $0.60 reservations = $6.00 total. Exactly
    # eight fit (8×0.60 = 4.80 ≤ 5.00; the 9th would hit 5.40 > 5.00).
    # The Lua check-and-set guarantees no over-admission under contention.
    monkeypatch.setattr(settings, "replicate_test_budget_usd", Decimal("5.00"))
    results = await asyncio.gather(
        *[
            spend_guard.check_and_reserve_session_budget(Decimal("0.60"), redis=rr)
            for _ in range(10)
        ]
    )
    assert sum(1 for ok in results if ok) == 8
    assert sum(1 for ok in results if not ok) == 2
    assert await spend_guard.get_session_spend(redis=rr) == Decimal("4.80")
