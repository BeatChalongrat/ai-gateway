"""Track A: async variant of ProviderKeyService.

Verifies ``get_api_key_async`` / ``get_active_key_async`` resolve keys via
the awaited DB read on a cache miss, honour the Phase-1 ``_keys_cache``,
and round-robin across the resolved candidate list — all on an
``AsyncSession`` over the seeded integration DB.
"""

import uuid

import pytest

from app.models.provider_key import ProviderKeyORM
from app.services import provider_key_service
from app.services.provider_key_service import ProviderKeyService, clear_keys_cache
import app.models  # noqa: F401


def _enc(raw: str) -> str:
    # FIX126 O8 — stored keys are AES-256-GCM (the resolve path refuses a
    # plain base64 blob as "not an AES-GCM encrypted value"). Encode the
    # fixture the way the service does, not the legacy way.
    from app.core.config import settings
    from app.core.security import encrypt_provider_key

    return encrypt_provider_key(raw, settings.effective_master_key)


@pytest.mark.asyncio
async def test_get_api_key_async_unknown_provider_returns_none(async_db):
    # No keys, no legacy row, no env var for this made-up provider -> None.
    clear_keys_cache()
    result = await ProviderKeyService().get_api_key_async(
        async_db, "no-such-provider-xyz"
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_api_key_async_resolves_db_key(async_db):
    clear_keys_cache()
    provider = f"asyncprov-{uuid.uuid4().hex[:8]}"
    async_db.add(
        ProviderKeyORM(
            id=str(uuid.uuid4()),
            provider_name=provider,
            label="k1",
            # Realistic-length key: the resolve path now filters dev
            # placeholders (Group A / A2), which rejects keys shorter than
            # 20 chars. A real provider key is long, so use one here.
            api_key_encrypted=_enc("sk-secret-1234567890abcdef"),
            is_active=True,
            last_test_status="valid",
        )
    )
    await async_db.flush()

    result = await ProviderKeyService().get_api_key_async(async_db, provider)
    assert result == "sk-secret-1234567890abcdef"


@pytest.mark.asyncio
async def test_get_active_key_async_uses_cache(async_db):
    # Prime the cache so a missing-provider stays None even after a row
    # would otherwise exist: the cache short-circuits the DB read.
    clear_keys_cache()
    provider = f"cacheprov-{uuid.uuid4().hex[:8]}"
    # First call: cache miss -> no candidates -> cached as empty list.
    assert await ProviderKeyService().get_active_key_async(async_db, provider) is None
    # The cache now holds an empty candidate list for this provider.
    assert provider in provider_key_service._keys_cache
    # Add a row AFTER caching; the cached empty list still wins (TTL).
    async_db.add(
        ProviderKeyORM(
            id=str(uuid.uuid4()),
            provider_name=provider,
            label="k1",
            api_key_encrypted=_enc("sk-late"),
            is_active=True,
            last_test_status="valid",
        )
    )
    await async_db.flush()
    assert await ProviderKeyService().get_active_key_async(async_db, provider) is None
    clear_keys_cache()
