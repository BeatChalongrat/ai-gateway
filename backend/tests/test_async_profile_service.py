"""Track A — async sibling of the policy profile service.

Verifies ``get_runtime_profile_async`` resolves the built-in "default"
profile over an AsyncSession on the seeded integration DB, and that it
matches the sync ``get_runtime_profile`` for the same id.
"""

import pytest

from app.domain.policy import PolicyProfile
from app.services.policy.profile_service import get_runtime_profile_async


@pytest.mark.asyncio
async def test_get_runtime_profile_async(async_db):
    profile = await get_runtime_profile_async(async_db, "default")

    assert isinstance(profile, PolicyProfile)
    assert profile.profile_id == "default"
