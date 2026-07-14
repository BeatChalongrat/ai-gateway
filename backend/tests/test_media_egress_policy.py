"""Per-tenant media-egress policy (Gateway Phase 9, A4).

Exercises the shared ``scan_media_prompt_with_profile`` helper — the
gate every media route funnels through (image/video/audio/music) — at
the service layer:

* credential/secret in the prompt → 403 regardless of tenant policy
  (unchanged pre-Phase-9 behavior);
* tenant ``media_pii_egress = "block"`` → identity PII in a prompt → 403;
* tenant ``allow`` (default) → identity PII passes AND writes a
  ``raw_egress`` audit row (categories only, no values);
* no-PII prompt → passes, no raw_egress flag;
* no ``tenant_id`` passed (legacy caller) → byte-for-byte old behavior
  (identity passes, secret blocks, no audit).
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
from app.services.media_gen.submit_helpers import scan_media_prompt_with_profile


PII_PROMPT = "a birthday portrait of สมชาย ใจดี, phone 081-234-5678"
SECRET_PROMPT = "an image showing the key sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
CLEAN_PROMPT = "a watercolor of a mountain lake at dawn"


async def _seed_tenant(async_db, *, media_pii_egress="allow"):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    async_db.add(TenantORM(
        tenant_id=tid, name="T", slug=tid, media_pii_egress=media_pii_egress,
    ))
    await async_db.commit()
    return tid


async def _raw_egress_rows(async_db, tenant_id):
    return (
        await async_db.execute(
            select(RequestLogORM).where(
                RequestLogORM.tenant_id == tenant_id,
                RequestLogORM.policy_action == "raw_egress_allow",
            )
        )
    ).scalars().all()


@pytest.mark.asyncio
async def test_secret_blocks_regardless_of_policy(async_db):
    tid = await _seed_tenant(async_db, media_pii_egress="allow")
    with pytest.raises(HTTPException) as exc:
        await scan_media_prompt_with_profile(
            SECRET_PROMPT, async_db, tenant_id=tid,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_block_policy_fails_closed_on_identity_pii(async_db):
    tid = await _seed_tenant(async_db, media_pii_egress="block")
    with pytest.raises(HTTPException) as exc:
        await scan_media_prompt_with_profile(
            PII_PROMPT, async_db, tenant_id=tid,
        )
    assert exc.value.status_code == 403
    # Nothing audited — the request was refused, not egressed.
    assert await _raw_egress_rows(async_db, tid) == []


@pytest.mark.asyncio
async def test_allow_policy_passes_and_audits(async_db):
    tid = await _seed_tenant(async_db, media_pii_egress="allow")
    # Passes (no raise).
    await scan_media_prompt_with_profile(
        PII_PROMPT, async_db,
        tenant_id=tid, project_id="p1", user_id="u1",
    )

    rows = await _raw_egress_rows(async_db, tid)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "media_gen"
    assert '"raw_egress": true' in (row.dlp_findings_json or "")
    # Categories only — no raw values.
    for fragment in ("สมชาย", "081-234-5678"):
        assert fragment not in (row.dlp_findings_json or "")
        assert fragment not in (row.dlp_categories or "")


@pytest.mark.asyncio
async def test_clean_prompt_passes_without_raw_egress_flag(async_db):
    tid = await _seed_tenant(async_db, media_pii_egress="allow")
    await scan_media_prompt_with_profile(
        CLEAN_PROMPT, async_db, tenant_id=tid,
    )
    assert await _raw_egress_rows(async_db, tid) == []


@pytest.mark.asyncio
async def test_legacy_caller_without_tenant_id_is_unchanged(async_db):
    # No tenant_id → old behavior: identity passes, secret blocks, no audit.
    # Delta-based (the shared integration DB may already hold raw_egress
    # rows committed by other suites' full-app route tests).
    async def _raw_count():
        return len(
            (
                await async_db.execute(
                    select(RequestLogORM).where(
                        RequestLogORM.policy_action == "raw_egress_allow"
                    )
                )
            ).scalars().all()
        )

    before = await _raw_count()

    await scan_media_prompt_with_profile(PII_PROMPT, async_db)  # identity passes
    with pytest.raises(HTTPException):
        await scan_media_prompt_with_profile(SECRET_PROMPT, async_db)  # secret blocks

    # The legacy (no-tenant) path audits NOTHING — the count is unchanged.
    assert await _raw_count() == before
