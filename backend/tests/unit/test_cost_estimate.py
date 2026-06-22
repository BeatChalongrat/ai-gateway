"""Tests for estimate_and_check_balance (Sprint 1 media-gen cost pre-check).

Asserts the job_type → cost-field mapping, tenant markup application
(reusing credit_service.credits_for_cost), the missing-cost-field guard,
and the balance check. Expected credit numbers are computed from the live
``usage_credits_per_usd`` setting so the test is environment-robust;
on the default 3,000,000 credits/USD a 5s $0.40/s clip = 6,000,000 credits.
"""

import math
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.gateway.portal_credit_check import estimate_and_check_balance


def _expected(cost_usd: float, markup: float) -> int:
    rate = int(settings.usage_credits_per_usd)
    return max(1, math.ceil(cost_usd * max(1.0, markup) * rate))


async def _seed(session, tenant_id, markup, balance):
    session.add(
        TenantORM(tenant_id=tenant_id, name=tenant_id, slug=tenant_id, credit_markup=markup)
    )
    uid = f"u-{tenant_id}"
    session.add(
        PortalUserORM(
            id=uid, email=f"{tenant_id}@e.com", password_hash="x", credit_balance=balance
        )
    )
    await session.flush()
    return uid


@pytest.mark.asyncio
async def test_video_cost_per_second(async_db):
    uid = await _seed(async_db, "t-vid", markup=1.0, balance=10_000_000)
    mc = SimpleNamespace(cost_per_second=0.40, cost_per_character=None, cost_per_generation=None)
    res = await estimate_and_check_balance(
        async_db, "t-vid", uid, "video", mc, {"duration_sec": 5}
    )
    assert res.estimated_credits == _expected(0.40 * 5, 1.0)
    assert res.sufficient is True


@pytest.mark.asyncio
async def test_tts_cost_per_character_with_markup(async_db):
    uid = await _seed(async_db, "t-tts", markup=2.0, balance=10_000_000)
    mc = SimpleNamespace(cost_per_second=None, cost_per_character=0.0001, cost_per_generation=None)
    res = await estimate_and_check_balance(
        async_db, "t-tts", uid, "audio_tts", mc, {"char_count": 500}
    )
    assert res.estimated_credits == _expected(0.0001 * 500, 2.0)


@pytest.mark.asyncio
async def test_music_cost_per_generation(async_db):
    uid = await _seed(async_db, "t-mus", markup=1.0, balance=10_000_000)
    mc = SimpleNamespace(cost_per_second=None, cost_per_character=None, cost_per_generation=0.02)
    res = await estimate_and_check_balance(
        async_db, "t-mus", uid, "music", mc, {}
    )
    assert res.estimated_credits == _expected(0.02, 1.0)


@pytest.mark.asyncio
async def test_missing_cost_field_raises(async_db):
    uid = await _seed(async_db, "t-bad", markup=1.0, balance=1000)
    mc = SimpleNamespace(cost_per_second=None, cost_per_character=None, cost_per_generation=None)
    with pytest.raises(ValueError):
        await estimate_and_check_balance(
            async_db, "t-bad", uid, "video", mc, {"duration_sec": 5}
        )


@pytest.mark.asyncio
async def test_insufficient_balance(async_db):
    uid = await _seed(async_db, "t-poor", markup=1.0, balance=100)
    mc = SimpleNamespace(cost_per_second=0.40, cost_per_character=None, cost_per_generation=None)
    res = await estimate_and_check_balance(
        async_db, "t-poor", uid, "video", mc, {"duration_sec": 5}
    )
    assert res.sufficient is False
    assert res.balance == 100
