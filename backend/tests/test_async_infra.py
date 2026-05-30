"""Track A.1 — async test-infra smoke tests.

Verifies the new async integration engine: (1) an AsyncSession can run
queries, and (2) it sees data on the SHARED sqlite file written by the
sync seed/setup — the invariant the post-flip chat hot path relies on.
"""

import pytest
from sqlalchemy import select, text

from app.models.project import ProjectORM


@pytest.mark.asyncio
async def test_async_session_executes(async_db):
    result = await async_db.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_async_session_sees_sync_seed(async_db):
    # The seed (test-tenant / test-project) is written by the SYNC engine
    # at conftest import; the async engine must see it via the shared file.
    res = await async_db.execute(
        select(ProjectORM).where(ProjectORM.project_id == "test-project")
    )
    proj = res.scalars().first()
    assert proj is not None
    assert proj.tenant_id == "test-tenant"
