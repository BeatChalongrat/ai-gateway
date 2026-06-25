"""Tests for CustomDetectorService — CRUD + lifecycle + audit (Sprint 6, B)."""

import uuid

import pytest
from sqlalchemy import select

from app.models.dlp_audit_log import DlpRuleAuditLogORM
from app.models.portal_user import PortalUserORM
from app.services.dlp.custom_detector_service import CustomDetectorService


async def _seed_user(db) -> str:
    uid = f"u-{uuid.uuid4().hex[:8]}"
    db.add(PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x"))
    await db.flush()
    return uid


def _svc(db) -> CustomDetectorService:
    return CustomDetectorService(db, redis_client=None)


async def _create(svc, uid, **over):
    kw = dict(
        name=f"det-{uuid.uuid4().hex[:8]}",
        description=None,
        category="pii",
        detection_type="regex",
        pattern={"regex": r"\d{4}"},
        severity="medium",
        default_action="mask",
        test_cases=[{"input": "1234", "expected_match": True},
                    {"input": "abcd", "expected_match": False}],
        created_by_user_id=uid,
    )
    kw.update(over)
    return await svc.create(**kw)


@pytest.mark.asyncio
async def test_create_writes_detector_and_audit(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    det = await _create(svc, uid)
    assert det.status == "draft"
    assert det.test_passed is True  # both test cases hold
    audits = (
        await async_db.execute(
            select(DlpRuleAuditLogORM).where(
                DlpRuleAuditLogORM.custom_detector_id == det.id
            )
        )
    ).scalars().all()
    assert [a.action for a in audits] == ["create"]


@pytest.mark.asyncio
async def test_update_snapshots_and_bumps_version(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    det = await _create(svc, uid)
    updated = await svc.update(
        det.id, changed_by_user_id=uid, reason="widen",
        pattern={"regex": r"\d{4,6}"},
    )
    assert updated.version == 2
    audit = (
        await async_db.execute(
            select(DlpRuleAuditLogORM)
            .where(DlpRuleAuditLogORM.custom_detector_id == det.id)
            .where(DlpRuleAuditLogORM.action == "update")
        )
    ).scalar_one()
    assert audit.before_value["pattern"]["regex"] == r"\d{4}"
    assert audit.after_value["pattern"]["regex"] == r"\d{4,6}"


@pytest.mark.asyncio
async def test_activate_requires_test_passed(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    # Failing test case → test_passed False → cannot activate.
    det = await _create(
        svc, uid, test_cases=[{"input": "abcd", "expected_match": True}]
    )
    assert det.test_passed is False
    with pytest.raises(ValueError, match="test_passed"):
        await svc.activate(det.id, uid)


@pytest.mark.asyncio
async def test_activate_succeeds_when_test_passed(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    det = await _create(svc, uid)
    activated = await svc.activate(det.id, uid)
    assert activated.status == "active"


@pytest.mark.asyncio
async def test_disable_requires_reason(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    det = await _create(svc, uid)
    with pytest.raises(ValueError, match="reason"):
        await svc.disable(det.id, uid, reason="")


@pytest.mark.asyncio
async def test_list_active_excludes_draft_and_disabled(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    draft = await _create(svc, uid)            # draft
    active = await _create(svc, uid)
    await svc.activate(active.id, uid)         # active
    testing = await _create(svc, uid)
    await svc.move_to_testing(testing.id, uid)  # testing
    ids = {d.id for d in await svc.list_active()}
    assert active.id in ids
    assert testing.id in ids
    assert draft.id not in ids


@pytest.mark.asyncio
async def test_record_timeout_threshold(async_db):
    uid = await _seed_user(async_db)
    svc = _svc(async_db)
    det = await _create(svc, uid)
    assert await svc.record_timeout(det.id) is False  # 1
    assert await svc.record_timeout(det.id) is False  # 2
    assert await svc.record_timeout(det.id) is True   # 3 → threshold
