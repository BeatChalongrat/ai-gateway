"""Track A: async variant of RequestLogService.persist.

``persist_async`` mirrors the sync ``persist`` contract exactly: it
``add``s a RequestLogORM and ``await``s a flush — it does NOT commit
(the caller owns the commit). These tests drive it against the seeded
async integration DB (``test-tenant`` / ``test-project``) and read the
row back through the same AsyncSession to prove it landed.
"""

import uuid

import pytest
from sqlalchemy import select

from app.domain.observability import RequestRecord, RequestStatus
from app.models.request_log import RequestLogORM
from app.services.request_log_service import RequestLogService
import app.models  # noqa: F401


def _record(request_id: str, **overrides) -> RequestRecord:
    base = dict(
        request_id=request_id,
        tenant_id="test-tenant",
        project_id="test-project",
        provider="openai",
        model="gpt-4o",
        status=RequestStatus.SUCCESS,
        policy_action="allow",
        latency_ms=150,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        estimated_cost_usd=0.001,
    )
    base.update(overrides)
    return RequestRecord(**base)


@pytest.mark.asyncio
async def test_persist_async_and_read_back(async_db):
    rid = f"req_async_{uuid.uuid4().hex[:8]}"
    await RequestLogService().persist_async(async_db, _record(rid))

    # Same session sees the flushed (uncommitted) row.
    row = await async_db.get(RequestLogORM, rid)
    assert row is not None
    assert row.provider == "openai"
    assert row.latency_ms == 150
    assert row.total_tokens == 15
    assert row.status == "success"


@pytest.mark.asyncio
async def test_persist_async_blocked_record(async_db):
    rid = f"req_async_block_{uuid.uuid4().hex[:8]}"
    await RequestLogService().persist_async(
        async_db,
        _record(
            rid,
            status=RequestStatus.BLOCKED,
            policy_action="block",
        ),
    )

    result = await async_db.execute(
        select(RequestLogORM).where(RequestLogORM.request_id == rid)
    )
    row = result.scalar_one()
    assert row.status == "blocked"
    assert row.policy_action == "block"
