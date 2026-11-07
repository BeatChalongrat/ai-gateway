"""Track A: async variant of QuotaService.

The seeded integration test-project has no max_requests_per_day, so the
async check is a no-op (must not raise).
"""

import pytest

from app.services.quota_service import QuotaService
import app.models  # noqa: F401


@pytest.mark.asyncio
async def test_check_request_quota_async_no_limit(async_db):
    # test-project has no quota configured -> no raise.
    await QuotaService().check_request_quota_async(async_db, "test-project")


@pytest.mark.asyncio
async def test_check_request_quota_async_missing_project(async_db):
    # Unknown project -> silently returns (no raise).
    await QuotaService().check_request_quota_async(async_db, "does-not-exist")
