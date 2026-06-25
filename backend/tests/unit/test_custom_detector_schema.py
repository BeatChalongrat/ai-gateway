"""Schema-level tests for custom_detectors + dlp_rule_audit_log (Sprint 6, A).

Runs on the SQLite test engine (Base.metadata.create_all), which enforces the
CHECK + UNIQUE constraints the migration declares.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.custom_detector import CustomDetectorORM
from app.models.dlp_audit_log import DlpRuleAuditLogORM
from app.models.portal_user import PortalUserORM


async def _seed_user(db) -> str:
    uid = f"u-{uuid.uuid4().hex[:8]}"
    db.add(PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x"))
    await db.flush()
    return uid


def _detector(uid, **overrides) -> CustomDetectorORM:
    fields = dict(
        id=str(uuid.uuid4()),
        name=f"det-{uuid.uuid4().hex[:8]}",
        category="pii",
        detection_type="regex",
        pattern={"regex": r"\d{4}"},
        created_by_user_id=uid,
    )
    fields.update(overrides)
    return CustomDetectorORM(**fields)


@pytest.mark.asyncio
async def test_insert_minimal_detector_succeeds(async_db):
    uid = await _seed_user(async_db)
    det = _detector(uid)
    async_db.add(det)
    await async_db.flush()
    row = (
        await async_db.execute(
            select(CustomDetectorORM).where(CustomDetectorORM.id == det.id)
        )
    ).scalar_one()
    # Server defaults applied.
    assert row.status == "draft"
    assert row.severity == "medium"
    assert row.default_action == "mask"
    assert row.test_passed is False
    assert row.version == 1


@pytest.mark.asyncio
async def test_invalid_status_rejected_by_check(async_db):
    uid = await _seed_user(async_db)
    async_db.add(_detector(uid, status="bogus"))
    with pytest.raises(IntegrityError):
        await async_db.flush()
    await async_db.rollback()


@pytest.mark.asyncio
async def test_duplicate_name_rejected(async_db):
    uid = await _seed_user(async_db)
    async_db.add(_detector(uid, name="dup-name"))
    await async_db.flush()
    async_db.add(_detector(uid, name="dup-name"))
    with pytest.raises(IntegrityError):
        await async_db.flush()
    await async_db.rollback()


@pytest.mark.asyncio
async def test_audit_log_row_with_fk(async_db):
    uid = await _seed_user(async_db)
    det = _detector(uid)
    async_db.add(det)
    await async_db.flush()
    audit = DlpRuleAuditLogORM(
        id=str(uuid.uuid4()),
        custom_detector_id=det.id,
        detector_name=det.name,
        action="create",
        changed_by_user_id=uid,
        after_value={"status": "draft"},
    )
    async_db.add(audit)
    await async_db.flush()
    row = (
        await async_db.execute(
            select(DlpRuleAuditLogORM).where(DlpRuleAuditLogORM.id == audit.id)
        )
    ).scalar_one()
    assert row.custom_detector_id == det.id
    assert row.action == "create"


@pytest.mark.asyncio
async def test_audit_survives_detector_delete(async_db):
    # Audit rows must outlive the detector (FK is ON DELETE SET NULL, never
    # CASCADE). The trail keeps a snapshot of the name.
    uid = await _seed_user(async_db)
    det = _detector(uid)
    async_db.add(det)
    await async_db.flush()
    audit = DlpRuleAuditLogORM(
        id=str(uuid.uuid4()),
        custom_detector_id=det.id,
        detector_name=det.name,
        action="delete",
        changed_by_user_id=uid,
    )
    async_db.add(audit)
    await async_db.flush()

    await async_db.delete(det)
    await async_db.flush()

    surviving = (
        await async_db.execute(
            select(DlpRuleAuditLogORM).where(DlpRuleAuditLogORM.id == audit.id)
        )
    ).scalar_one()
    assert surviving.detector_name == det.name  # snapshot preserved

    # The FK is declared ON DELETE SET NULL (independent of SQLite's
    # runtime FK-pragma, which is off in the test engine).
    fk = next(iter(DlpRuleAuditLogORM.__table__.c.custom_detector_id.foreign_keys))
    assert fk.ondelete == "SET NULL"
