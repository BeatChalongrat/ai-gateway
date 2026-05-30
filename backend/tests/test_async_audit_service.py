"""Track A: async variants of AuditService.record_blocked_request /
record_success.

IMPORTANT contract note: the sync ``record_blocked_request`` and
``record_success`` methods are LOG-ONLY today — they emit privacy-safe
DLP audit entries + a structlog event and write NO database row (general
request logging / DB persistence are deferred to Phase 7). The async
variants therefore have nothing to ``await``; they exist so the async
hot path can call them uniformly and accept an optional ``session``
hook for the future Phase 7 audit-row write.

These tests confirm:
- the async variants accept an ``async_db`` AsyncSession without error,
- logic parity with the sync version (entries returned / emitted),
- the no-finding path returns no entries.
"""

import pytest

from app.domain.dlp import (
    DetectionMatch,
    EnforcementAction,
    EnforcementDecision,
    MatchAction,
    SensitivityLevel,
)
from app.domain.entities import RequestContext
from app.services.audit_service import AuditService


def _ctx(**kwargs) -> RequestContext:
    defaults = {"tenant_id": "test-tenant", "project_id": "test-project", "project_name": "Test"}
    defaults.update(kwargs)
    return RequestContext(**defaults)


def _match_action() -> MatchAction:
    return MatchAction(
        match=DetectionMatch(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            start=10,
            end=26,
            matched_text="user@example.com",
            severity=SensitivityLevel.MEDIUM,
        ),
        action=EnforcementAction.MASK,
        reason_code="test_policy",
        reason_message="Test reason",
    )


def _enforcement(*mas: MatchAction) -> EnforcementDecision:
    return EnforcementDecision(
        match_actions=list(mas),
        policy_profile_id="default",
        reasons=["Test reason"],
    )


@pytest.mark.asyncio
async def test_record_blocked_request_async_returns_entries(async_db):
    enforcement = _enforcement(_match_action())
    entries = await AuditService().record_blocked_request_async(
        ctx=_ctx(),
        enforcement=enforcement,
        direction="input",
        session=async_db,
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.detector_name == "email"
    assert entry.direction == "input"
    # Privacy guarantee: no matched_text leaks onto the audit entry.
    assert not hasattr(entry, "matched_text") or getattr(entry, "matched_text", None) is None


@pytest.mark.asyncio
async def test_record_blocked_request_async_no_session(async_db):
    # session is optional — call without it (mirrors today's log-only call sites).
    entries = await AuditService().record_blocked_request_async(
        ctx=_ctx(),
        enforcement=_enforcement(),
        direction="output",
    )
    assert entries == []


@pytest.mark.asyncio
async def test_record_success_async_accepts_session(async_db):
    enforcement = _enforcement(_match_action())
    # Returns None (log-only); must not raise with an AsyncSession passed.
    result = await AuditService().record_success_async(
        ctx=_ctx(),
        route=None,
        enforcement=enforcement,
        usage=None,
        input_enforcement=enforcement,
        output_enforcement=None,
        session=async_db,
    )
    assert result is None
