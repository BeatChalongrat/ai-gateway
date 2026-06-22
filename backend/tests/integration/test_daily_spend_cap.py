"""L2 tenant daily-spend cap — Sprint 2B.

Real Redis (DB 15) for the counter + the SQLite async test DB for the
tenant's ``max_daily_spend_usd``.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.models.tenant import TenantORM
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


async def _tenant(async_db, cap=None):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    kw = {}
    if cap is not None:
        kw["max_daily_spend_usd"] = Decimal(cap)
    async_db.add(TenantORM(tenant_id=tid, name=tid, slug=tid, **kw))
    await async_db.flush()
    return tid


def _key(tid):
    return spend_guard.TENANT_KEY.format(tenant_id=tid, date=spend_guard._today())


@pytest.mark.asyncio
async def test_under_cap_allowed(async_db, rr):
    tid = await _tenant(async_db, cap="10.00")
    await rr.set(_key(tid), "9.00")
    res = await spend_guard.check_daily_spend_cap(async_db, tid, Decimal("0.50"), redis=rr)
    assert res.allowed is True
    assert res.cap == Decimal("10.00")
    assert res.would_be == Decimal("9.50")


@pytest.mark.asyncio
async def test_over_cap_rejected(async_db, rr):
    tid = await _tenant(async_db, cap="10.00")
    await rr.set(_key(tid), "9.80")
    res = await spend_guard.check_daily_spend_cap(async_db, tid, Decimal("0.50"), redis=rr)
    assert res.allowed is False  # 9.80 + 0.50 = 10.30 > 10.00
    assert res.would_be == Decimal("10.30")


@pytest.mark.asyncio
async def test_default_cap_for_tenant_without_value(async_db, rr):
    # A tenant whose column defaulted to 10.00 (server_default) — simulate by
    # not setting it; the ORM default applies on insert.
    tid = await _tenant(async_db)
    await async_db.commit()
    res = await spend_guard.check_daily_spend_cap(async_db, tid, Decimal("0.50"), redis=rr)
    assert res.cap == Decimal("10.00")
    assert res.allowed is True


@pytest.mark.asyncio
async def test_commit_increments_counter(async_db, rr):
    tid = await _tenant(async_db, cap="10.00")
    await spend_guard.commit_daily_spend(tid, Decimal("0.35"), redis=rr)
    await spend_guard.commit_daily_spend(tid, Decimal("0.35"), redis=rr)
    val = await rr.get(_key(tid))
    assert Decimal(str(val)) == Decimal("0.70")
    ttl = await rr.ttl(_key(tid))
    assert 0 < ttl <= 86400
