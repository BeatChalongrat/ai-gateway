"""Track A: async variant of ProjectService.

Verifies resolve_project_context_async returns the same RequestContext
the sync version would for the seeded integration test-project.
"""

import pytest

from app.api.deps import ResolvedApiKey
from app.domain.entities import RequestContext
from app.services.project_service import ProjectService
import app.models  # noqa: F401


@pytest.mark.asyncio
async def test_resolve_project_context_async_seeded(async_db):
    auth = ResolvedApiKey(
        key_id="test-key-id",
        tenant_id="test-tenant",
        project_id="test-project",
        key_prefix="gw_test_",
        role="admin",
    )

    ctx = await ProjectService().resolve_project_context_async(
        async_db, auth, request_id="req-async-1"
    )

    assert isinstance(ctx, RequestContext)
    assert ctx.tenant_id == "test-tenant"
    assert ctx.project_id == "test-project"
    assert ctx.project_name == "Test Project"
    assert ctx.policy_profile_id == "default"
    assert ctx.request_id == "req-async-1"
