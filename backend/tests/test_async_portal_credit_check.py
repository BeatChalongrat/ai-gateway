"""Track A: async variants of the portal credit gateway hooks.

Covers ``check_portal_credit_async`` (no-op for non-portal keys, 403 for
suspended owner, 429 when out of credits) and ``deduct_portal_credit_async``
(clamped balance decrement + ledger row), all on an ``AsyncSession`` over
the seeded integration DB.
"""

import uuid

import pytest

from app.core.exceptions import AuthorizationError, QuotaExceededError
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.services.gateway.portal_credit_check import (
    check_portal_credit_async,
    deduct_portal_credit_async,
)
import app.models  # noqa: F401
from sqlalchemy import select


async def _seed_user(async_db, *, status="active", balance=1000):
    user = PortalUserORM(
        id=str(uuid.uuid4()),
        email=f"async-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        status=status,
        credit_balance=balance,
        credit_used=0,
    )
    async_db.add(user)
    await async_db.flush()
    return user


async def _seed_key(async_db, portal_user_id=None):
    key = ApiKeyORM(
        key_id=str(uuid.uuid4()),
        key_prefix="gw_test_",
        key_hash=uuid.uuid4().hex,
        tenant_id="test-tenant",
        project_id="test-project",
        name="async-credit-test",
        status="active",
        portal_user_id=portal_user_id,
    )
    async_db.add(key)
    await async_db.flush()
    return key


@pytest.mark.asyncio
async def test_check_portal_credit_async_noop_for_jwt_key(async_db):
    # jwt: prefixed ids never exist in api_keys -> silent no-op.
    await check_portal_credit_async(async_db, "jwt:" + uuid.uuid4().hex)


@pytest.mark.asyncio
async def test_check_portal_credit_async_noop_for_non_portal_key(async_db):
    key = await _seed_key(async_db, portal_user_id=None)
    await check_portal_credit_async(async_db, key.key_id)  # no raise


@pytest.mark.asyncio
async def test_check_portal_credit_async_suspended_raises(async_db):
    user = await _seed_user(async_db, status="suspended", balance=500)
    key = await _seed_key(async_db, portal_user_id=user.id)
    with pytest.raises(AuthorizationError):
        await check_portal_credit_async(async_db, key.key_id)


@pytest.mark.asyncio
async def test_check_portal_credit_async_no_credits_raises(async_db):
    user = await _seed_user(async_db, status="active", balance=0)
    key = await _seed_key(async_db, portal_user_id=user.id)
    with pytest.raises(QuotaExceededError):
        await check_portal_credit_async(async_db, key.key_id)


@pytest.mark.asyncio
async def test_deduct_portal_credit_async_writes_ledger_and_decrements(async_db):
    user = await _seed_user(async_db, status="active", balance=1000)
    key = await _seed_key(async_db, portal_user_id=user.id)

    req_id = uuid.uuid4().hex
    await deduct_portal_credit_async(async_db, key.key_id, 300, req_id)

    refreshed = await async_db.get(PortalUserORM, user.id)
    assert int(refreshed.credit_balance) == 700
    assert int(refreshed.credit_used) == 300

    tx = (
        await async_db.execute(
            select(CreditTransactionORM).where(
                CreditTransactionORM.request_id == req_id
            )
        )
    ).scalar_one()
    assert tx.amount == -300
    assert tx.balance_after == 700
    assert tx.type == "usage"


@pytest.mark.asyncio
async def test_deduct_portal_credit_async_clamps_at_zero(async_db):
    user = await _seed_user(async_db, status="active", balance=100)
    key = await _seed_key(async_db, portal_user_id=user.id)

    await deduct_portal_credit_async(async_db, key.key_id, 999, uuid.uuid4().hex)

    refreshed = await async_db.get(PortalUserORM, user.id)
    assert int(refreshed.credit_balance) == 0
    assert int(refreshed.credit_used) == 100  # only the actual amount


@pytest.mark.asyncio
async def test_deduct_portal_credit_async_noop_zero_tokens(async_db):
    user = await _seed_user(async_db, status="active", balance=500)
    key = await _seed_key(async_db, portal_user_id=user.id)

    await deduct_portal_credit_async(async_db, key.key_id, 0, uuid.uuid4().hex)

    refreshed = await async_db.get(PortalUserORM, user.id)
    assert int(refreshed.credit_balance) == 500
