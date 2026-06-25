"""CRUD + lifecycle + audit for admin-defined DLP detectors (Sprint 6, B).

This is the data-access + state-machine layer. It does NOT execute detectors
on traffic — that's the loader/registry (D/E). Every mutation writes a
``dlp_rule_audit_log`` row in the same transaction and (when a Redis client is
wired in by D) publishes a hot-reload signal so every worker drops its cache.

Lifecycle state machine::

    draft ──move_to_testing──▶ testing ──activate──▶ active
      │                          │                     │
      └──────activate (needs test_passed)──────────────┤
                                                        │
    any ──────────────disable(reason)──────────────▶ disabled

``activate`` requires ``test_passed=True``. ``disable``/``delete`` require a
reason (audited). Only ``testing`` + ``active`` rows ever run on traffic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_detector import CustomDetectorORM
from app.models.dlp_audit_log import DlpRuleAuditLogORM
from app.services.dlp.custom_detector_matching import run_matches
from app.services.dlp.pattern_validator import validate_pattern

_TIMEOUT_DISABLE_THRESHOLD = 3


async def write_audit_log(
    db: AsyncSession,
    detector_id: Optional[str],
    detector_name: str,
    action: str,
    user_id: str,
    *,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    reason: Optional[str] = None,
) -> None:
    """Append one audit entry. Called from service methods, never from routes."""
    db.add(
        DlpRuleAuditLogORM(
            id=str(uuid.uuid4()),
            custom_detector_id=detector_id,
            detector_name=detector_name,
            action=action,
            changed_by_user_id=user_id,
            before_value=before,
            after_value=after,
            reason=reason,
        )
    )


def evaluate_test_cases(
    detection_type: str, pattern: dict, test_cases: list[dict]
) -> bool:
    """Return True iff there is >=1 test case and ALL of them hold.

    A test case is ``{"input": str, "expected_match": bool}``. Empty test
    sets return False — a detector can't be activated without proof.
    """
    if not test_cases:
        return False
    for tc in test_cases:
        text = str(tc.get("input", ""))
        expected = bool(tc.get("expected_match", False))
        try:
            matched = len(run_matches(detection_type, pattern, text)) > 0
        except TimeoutError:
            return False
        if matched != expected:
            return False
    return True


class CustomDetectorService:
    def __init__(self, db: AsyncSession, redis_client=None) -> None:
        self._db = db
        self._redis = redis_client

    # -- internal helpers ---------------------------------------------------

    async def _publish_reload(self) -> None:
        if self._redis is None:
            return
        try:
            from app.services.dlp.custom_detector_loader import publish_reload

            await publish_reload(self._redis)
        except Exception:  # noqa: BLE001 — reload signal is best-effort
            pass

    async def get(self, detector_id: str) -> Optional[CustomDetectorORM]:
        return await self._db.get(CustomDetectorORM, detector_id)

    # -- CRUD ---------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        description: Optional[str],
        category: str,
        detection_type: str,
        pattern: dict,
        severity: str,
        default_action: str,
        test_cases: Optional[list[dict]],
        created_by_user_id: str,
    ) -> CustomDetectorORM:
        """Validate the pattern, run test cases, insert as draft, audit."""
        vr = validate_pattern(detection_type, pattern)
        if not vr.valid:
            raise ValueError(vr.error or "invalid pattern")

        test_cases = test_cases or []
        test_passed = evaluate_test_cases(detection_type, pattern, test_cases)

        det = CustomDetectorORM(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            category=category,
            detection_type=detection_type,
            pattern=pattern,
            severity=severity,
            default_action=default_action,
            status="draft",
            test_cases=test_cases,
            test_passed=test_passed,
            created_by_user_id=created_by_user_id,
        )
        self._db.add(det)
        await self._db.flush()
        await write_audit_log(
            self._db, det.id, det.name, "create", created_by_user_id,
            after=det.to_dict(),
        )
        await self._db.commit()
        await self._publish_reload()
        return det

    async def update(
        self,
        detector_id: str,
        *,
        changed_by_user_id: str,
        reason: Optional[str] = None,
        **fields,
    ) -> CustomDetectorORM:
        """Update mutable fields, snapshot before-value, bump version.

        Status is NOT mutable here — use the lifecycle methods.
        """
        det = await self._require(detector_id)
        before = det.to_dict()

        new_type = fields.get("detection_type", det.detection_type)
        new_pattern = fields.get("pattern", det.pattern)
        if "pattern" in fields or "detection_type" in fields:
            vr = validate_pattern(new_type, new_pattern)
            if not vr.valid:
                raise ValueError(vr.error or "invalid pattern")

        mutable = {
            "name", "description", "category", "detection_type", "pattern",
            "severity", "default_action", "test_cases",
        }
        for key, value in fields.items():
            if key in mutable:
                setattr(det, key, value)

        # Re-evaluate test_passed against the (possibly new) pattern + cases.
        det.test_passed = evaluate_test_cases(
            det.detection_type, det.pattern, det.test_cases or []
        )
        det.version = det.version + 1
        det.updated_at = datetime.now(timezone.utc)
        await self._db.flush()
        await write_audit_log(
            self._db, det.id, det.name, "update", changed_by_user_id,
            before=before, after=det.to_dict(), reason=reason,
        )
        await self._db.commit()
        await self._publish_reload()
        return det

    # -- Lifecycle ----------------------------------------------------------

    async def activate(
        self, detector_id: str, changed_by_user_id: str
    ) -> CustomDetectorORM:
        det = await self._require(detector_id)
        if not det.test_passed:
            raise ValueError("cannot activate: test_passed is False")
        return await self._transition(det, "active", changed_by_user_id, "activate")

    async def move_to_testing(
        self, detector_id: str, changed_by_user_id: str
    ) -> CustomDetectorORM:
        det = await self._require(detector_id)
        return await self._transition(
            det, "testing", changed_by_user_id, "move_to_testing"
        )

    async def disable(
        self, detector_id: str, changed_by_user_id: str, reason: str
    ) -> CustomDetectorORM:
        if not reason or not reason.strip():
            raise ValueError("disable requires a reason")
        det = await self._require(detector_id)
        return await self._transition(
            det, "disabled", changed_by_user_id, "disable", reason=reason
        )

    async def delete(
        self, detector_id: str, changed_by_user_id: str, reason: str
    ) -> None:
        """Hard delete; the audit trail (FK SET NULL) keeps a name snapshot."""
        if not reason or not reason.strip():
            raise ValueError("delete requires a reason")
        det = await self._require(detector_id)
        before = det.to_dict()
        name = det.name
        det_id = det.id
        await write_audit_log(
            self._db, det_id, name, "delete", changed_by_user_id,
            before=before, reason=reason,
        )
        await self._db.delete(det)
        await self._db.commit()
        await self._publish_reload()

    # -- Queries / metrics --------------------------------------------------

    async def list_all(
        self, status: Optional[str] = None
    ) -> list[CustomDetectorORM]:
        stmt = select(CustomDetectorORM)
        if status:
            stmt = stmt.where(CustomDetectorORM.status == status)
        stmt = stmt.order_by(CustomDetectorORM.created_at.desc())
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_active(self) -> list[CustomDetectorORM]:
        """active + testing rows — the set the loader caches."""
        stmt = select(CustomDetectorORM).where(
            CustomDetectorORM.status.in_(("active", "testing"))
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def audit_log(
        self, detector_id: str, limit: int = 50
    ) -> list[DlpRuleAuditLogORM]:
        stmt = (
            select(DlpRuleAuditLogORM)
            .where(DlpRuleAuditLogORM.custom_detector_id == detector_id)
            .order_by(DlpRuleAuditLogORM.changed_at.desc())
            .limit(limit)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def record_match(self, detector_id: str) -> None:
        """Fire-and-forget: bump match count + last_matched_at."""
        det = await self._db.get(CustomDetectorORM, detector_id)
        if det is None:
            return
        det.last_match_count = (det.last_match_count or 0) + 1
        det.last_matched_at = datetime.now(timezone.utc)
        await self._db.commit()

    async def record_timeout(self, detector_id: str) -> bool:
        """Bump timeout_failures. Return True when the disable threshold (3)
        is reached so the caller can auto-disable."""
        det = await self._db.get(CustomDetectorORM, detector_id)
        if det is None:
            return False
        det.timeout_failures = (det.timeout_failures or 0) + 1
        reached = det.timeout_failures >= _TIMEOUT_DISABLE_THRESHOLD
        await self._db.commit()
        return reached

    # -- shared --------------------------------------------------------------

    async def _require(self, detector_id: str) -> CustomDetectorORM:
        det = await self._db.get(CustomDetectorORM, detector_id)
        if det is None:
            raise ValueError(f"detector not found: {detector_id}")
        return det

    async def _transition(
        self,
        det: CustomDetectorORM,
        new_status: str,
        user_id: str,
        action: str,
        *,
        reason: Optional[str] = None,
    ) -> CustomDetectorORM:
        before = det.to_dict()
        det.status = new_status
        det.updated_at = datetime.now(timezone.utc)
        await self._db.flush()
        await write_audit_log(
            self._db, det.id, det.name, action, user_id,
            before=before, after=det.to_dict(), reason=reason,
        )
        await self._db.commit()
        await self._publish_reload()
        return det
