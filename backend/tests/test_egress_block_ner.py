"""Fail-closed NER scan on the "block" egress policy (Phase 10, A2).

A tenant with ``search_pii_egress`` / ``media_pii_egress = "block"``
promises "fail closed on ANY identity PII". Names are NER-only findings —
before this fix both raw-egress scans ran ``ner_enabled=False``
unconditionally, so a name-only query/prompt produced ZERO detections,
``enforcement.match_actions`` was empty, and the content passed through
raw despite the block policy. Emails/phones/Thai-IDs (regex) blocked
correctly; names leaked.

The fix: run the scan with ``ner_enabled=True`` when (and only when) the
tenant's policy is ``block``. These tests pin the MECHANISM by stubbing
``GatewayOrchestrator._scan_text_as_result_async`` — the stub returns a
PERSON finding only when the NER axis is on, exactly the delta between
the two production registries — so they are deterministic on hosts with
or without presidio/spaCy (the ``ner`` marker convention in conftest).

Also pins the cost contract: the ``allow`` path still scans with NER OFF
(byte-for-byte unchanged), so the extra NER cost applies only to block
tenants.
"""

import uuid

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from app.core.exceptions import SearchQueryBlockedError
from app.domain.dlp import DetectionMatch, DetectionResult, SensitivityLevel
from app.models.tenant import TenantORM
from app.services.dlp.egress_policy import scan_search_query
from app.services.media_gen.submit_helpers import scan_media_prompt_with_profile
from app.services.orchestrator import GatewayOrchestrator

# A bare name — NER-only. No regex detector (phone/email/ID/credential)
# fires on this text, and it carries none of the context keywords the
# thai_name_no_prefix fallback gates on.
NAME_ONLY_TEXT = "ประวัติของ สมชาย"


@pytest.fixture
def ner_gated_scan(monkeypatch):
    """Stub the orchestrator scan: a PERSON match exists ONLY when the
    scan runs with ``ner_enabled=True`` (mirrors the presidio-vs-regex
    registry delta). Records each call's ner_enabled flag."""
    calls: list[bool] = []

    async def _fake_scan(self, text, direction="output", *, ner_enabled=True):
        calls.append(ner_enabled)
        matches = []
        if ner_enabled and "สมชาย" in (text or ""):
            start = text.index("สมชาย")
            matches.append(
                DetectionMatch(
                    detector_name="presidio_ner",
                    entity_type="person",
                    category="pii",
                    start=start,
                    end=start + len("สมชาย"),
                    matched_text="สมชาย",
                    severity=SensitivityLevel.MEDIUM,
                    confidence=0.85,
                )
            )
        return DetectionResult(matches=matches, scanned_direction=direction)

    monkeypatch.setattr(
        GatewayOrchestrator, "_scan_text_as_result_async", _fake_scan
    )
    return calls


async def _seed_tenant(async_db, **cols):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(tenant_id=tid, name="T", slug=tid, **cols))
    await async_db.commit()
    return tid


# ---------------------------------------------------------------------------
# Search channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_block_tenant_blocks_name_only_query(async_db, ner_gated_scan):
    tenant = SimpleNamespace(tenant_id="t-block", search_pii_egress="block")
    with pytest.raises(SearchQueryBlockedError):
        await scan_search_query(NAME_ONLY_TEXT, async_db, tenant)
    # The block path ran the scan with NER ON — that's what caught the name.
    assert ner_gated_scan == [True]


@pytest.mark.asyncio
async def test_search_allow_tenant_still_scans_without_ner(async_db, ner_gated_scan):
    tenant = SimpleNamespace(tenant_id="t-allow", search_pii_egress="allow")
    # Name-only query passes (raw by design) — no exception.
    await scan_search_query(NAME_ONLY_TEXT, async_db, tenant)
    # And the allow path is byte-for-byte unchanged: NER stayed OFF.
    assert ner_gated_scan == [False]


# ---------------------------------------------------------------------------
# Media channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_block_tenant_blocks_name_only_prompt(async_db, ner_gated_scan):
    tid = await _seed_tenant(async_db, media_pii_egress="block")
    with pytest.raises(HTTPException) as exc:
        await scan_media_prompt_with_profile(
            NAME_ONLY_TEXT, async_db, tenant_id=tid
        )
    assert exc.value.status_code == 403
    assert ner_gated_scan == [True]


@pytest.mark.asyncio
async def test_media_allow_tenant_still_scans_without_ner(async_db, ner_gated_scan):
    tid = await _seed_tenant(async_db, media_pii_egress="allow")
    await scan_media_prompt_with_profile(NAME_ONLY_TEXT, async_db, tenant_id=tid)
    assert ner_gated_scan == [False]


@pytest.mark.asyncio
async def test_media_legacy_no_tenant_still_scans_without_ner(
    async_db, ner_gated_scan
):
    # Legacy caller (no tenant_id): identity passes, NER stays off.
    await scan_media_prompt_with_profile(NAME_ONLY_TEXT, async_db)
    assert ner_gated_scan == [False]


# ---------------------------------------------------------------------------
# Regression pin: credential class still blocks on the allow path
# (real detectors — no scan stub here).
# ---------------------------------------------------------------------------

SECRET_TEXT = "generate art of my key sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"


@pytest.mark.asyncio
async def test_allow_tenant_credential_still_blocks_media(async_db):
    tid = await _seed_tenant(async_db, media_pii_egress="allow")
    with pytest.raises(HTTPException) as exc:
        await scan_media_prompt_with_profile(SECRET_TEXT, async_db, tenant_id=tid)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_allow_tenant_credential_still_blocks_search(async_db):
    tenant = SimpleNamespace(tenant_id="t-allow", search_pii_egress="allow")
    with pytest.raises(SearchQueryBlockedError):
        await scan_search_query(SECRET_TEXT, async_db, tenant)
