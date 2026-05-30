"""Track A: async variant of ApiKeyService.verify_key.

Verifies ``verify_key_async``: missing key -> None, a valid active key ->
the ORM row, a revoked key -> None — all on an ``AsyncSession`` over the
seeded integration DB. Rows are minted with the sync ``create_key`` helper
through a throwaway sync session would couple drivers, so we seed directly
via the async session.
"""

import uuid

import pytest

from app.core.security import generate_api_key
from app.models.api_key import ApiKeyORM
from app.services.api_key_service import ApiKeyService
import app.models  # noqa: F401


async def _seed_key(async_db, status: str = "active"):
    raw_key, key_prefix, key_hash = generate_api_key()
    row = ApiKeyORM(
        key_id=str(uuid.uuid4()),
        key_prefix=key_prefix,
        key_hash=key_hash,
        tenant_id="test-tenant",
        project_id="test-project",
        name=f"async-test-{uuid.uuid4().hex[:6]}",
        status=status,
    )
    async_db.add(row)
    await async_db.flush()
    return raw_key, row


@pytest.mark.asyncio
async def test_verify_key_async_missing_returns_none(async_db):
    result = await ApiKeyService().verify_key_async(async_db, "gw_not_a_real_key")
    assert result is None


@pytest.mark.asyncio
async def test_verify_key_async_active_returns_row(async_db):
    raw_key, row = await _seed_key(async_db, status="active")
    result = await ApiKeyService().verify_key_async(async_db, raw_key)
    assert result is not None
    assert result.key_id == row.key_id
    # First verify writes last_used_at (debounce had no prior entry).
    assert result.last_used_at is not None


@pytest.mark.asyncio
async def test_verify_key_async_revoked_returns_none(async_db):
    raw_key, _row = await _seed_key(async_db, status="revoked")
    result = await ApiKeyService().verify_key_async(async_db, raw_key)
    assert result is None
