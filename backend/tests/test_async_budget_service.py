"""Track A: async variant of BudgetService.

The seeded integration test-project has no budget_limit, so the async
check is a no-op (must not raise).
"""

import pytest

from app.services.budget_service import BudgetService
import app.models  # noqa: F401


@pytest.mark.asyncio
async def test_check_budget_async_no_limit(async_db):
    # test-project has no budget_limit configured -> no raise.
    await BudgetService().check_budget_async(async_db, "test-project")


@pytest.mark.asyncio
async def test_check_budget_async_missing_project(async_db):
    # Unknown project -> silently returns (no raise).
    await BudgetService().check_budget_async(async_db, "does-not-exist")
