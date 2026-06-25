"""Admin CRUD API for custom DLP detectors (Sprint 6, F).

Mounted at ``/admin/dlp/custom-detectors`` with ``require_admin_session`` so
every route is admin-only. All mutations go through ``CustomDetectorService``
(which writes the audit log + publishes the Redis hot-reload signal); routes
never touch the audit table or Redis directly.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, require_admin_session
from app.core.db import get_async_db
from app.schemas.dlp_admin import (
    AuditLogEntry,
    CreateDetectorRequest,
    CustomDetectorResponse,
    DisableDetectorRequest,
    TestDetectorRequest,
    TestDetectorResponse,
    TestDetectorResult,
    UpdateDetectorRequest,
)
from app.services.dlp.custom_detector_matching import run_matches
from app.services.dlp.custom_detector_service import CustomDetectorService

router = APIRouter(
    prefix="/dlp/custom-detectors",
    tags=["dlp-admin"],
    dependencies=[Depends(require_admin_session)],
)


def _actor_id(auth: ResolvedApiKey) -> str:
    """The acting admin's id (strip the ``session:`` key prefix)."""
    kid = getattr(auth, "key_id", "") or ""
    return kid.split("session:", 1)[-1] if kid.startswith("session:") else kid


def _service(session: AsyncSession) -> CustomDetectorService:
    # A redis client so mutations fan out a hot-reload signal. Best-effort —
    # the service swallows publish errors.
    try:
        from app.services._redis_pool import get_async_redis

        redis = get_async_redis()
    except Exception:  # noqa: BLE001
        redis = None
    return CustomDetectorService(session, redis_client=redis)


def _require(detector):
    if detector is None:
        raise HTTPException(status_code=404, detail="detector not found")
    return detector


@router.get("", response_model=list[CustomDetectorResponse])
async def list_detectors(
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_async_db),
) -> list[CustomDetectorResponse]:
    svc = _service(session)
    rows = await svc.list_all(status=status)
    return [CustomDetectorResponse.from_orm_row(r) for r in rows]


@router.post("", response_model=CustomDetectorResponse)
async def create_detector(
    payload: CreateDetectorRequest,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> CustomDetectorResponse:
    svc = _service(session)
    try:
        det = await svc.create(
            name=payload.name,
            description=payload.description,
            category=payload.category,
            detection_type=payload.detection_type,
            pattern=payload.pattern,
            severity=payload.severity,
            default_action=payload.default_action,
            test_cases=[tc.model_dump() for tc in payload.test_cases],
            created_by_user_id=_actor_id(auth),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CustomDetectorResponse.from_orm_row(det)


@router.put("/{detector_id}", response_model=CustomDetectorResponse)
async def update_detector(
    detector_id: str,
    payload: UpdateDetectorRequest,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> CustomDetectorResponse:
    svc = _service(session)
    _require(await svc.get(detector_id))
    fields = payload.model_dump(exclude_unset=True, exclude={"reason"})
    if "test_cases" in fields and fields["test_cases"] is not None:
        fields["test_cases"] = [
            tc if isinstance(tc, dict) else tc.model_dump()
            for tc in fields["test_cases"]
        ]
    try:
        det = await svc.update(
            detector_id, changed_by_user_id=_actor_id(auth),
            reason=payload.reason, **fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CustomDetectorResponse.from_orm_row(det)


@router.post("/{detector_id}/activate", response_model=CustomDetectorResponse)
async def activate_detector(
    detector_id: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> CustomDetectorResponse:
    svc = _service(session)
    _require(await svc.get(detector_id))
    try:
        det = await svc.activate(detector_id, _actor_id(auth))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CustomDetectorResponse.from_orm_row(det)


@router.post("/{detector_id}/testing", response_model=CustomDetectorResponse)
async def move_to_testing(
    detector_id: str,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> CustomDetectorResponse:
    svc = _service(session)
    _require(await svc.get(detector_id))
    det = await svc.move_to_testing(detector_id, _actor_id(auth))
    return CustomDetectorResponse.from_orm_row(det)


@router.post("/{detector_id}/disable", response_model=CustomDetectorResponse)
async def disable_detector(
    detector_id: str,
    payload: DisableDetectorRequest,
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> CustomDetectorResponse:
    svc = _service(session)
    _require(await svc.get(detector_id))
    try:
        det = await svc.disable(detector_id, _actor_id(auth), reason=payload.reason or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CustomDetectorResponse.from_orm_row(det)


@router.delete("/{detector_id}")
async def delete_detector(
    detector_id: str,
    reason: str = Query(...),
    auth: ResolvedApiKey = Depends(require_admin_session),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    svc = _service(session)
    _require(await svc.get(detector_id))
    try:
        await svc.delete(detector_id, _actor_id(auth), reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": detector_id}


@router.post("/{detector_id}/test", response_model=TestDetectorResponse)
async def test_detector(
    detector_id: str,
    payload: TestDetectorRequest,
    session: AsyncSession = Depends(get_async_db),
) -> TestDetectorResponse:
    svc = _service(session)
    det = _require(await svc.get(detector_id))
    results: list[TestDetectorResult] = []
    for text in payload.inputs:
        t0 = time.monotonic()
        try:
            matched = len(run_matches(det.detection_type, det.pattern, text)) > 0
        except TimeoutError:
            matched = False
        results.append(
            TestDetectorResult(
                input=text, matched=matched,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        )
    return TestDetectorResponse(detector_id=detector_id, results=results)


@router.get("/{detector_id}/audit-log", response_model=list[AuditLogEntry])
async def get_audit_log(
    detector_id: str,
    limit: int = Query(default=50, le=500),
    session: AsyncSession = Depends(get_async_db),
) -> list[AuditLogEntry]:
    svc = _service(session)
    rows = await svc.audit_log(detector_id, limit=limit)
    return [
        AuditLogEntry(
            id=r.id, action=r.action, detector_name=r.detector_name,
            changed_by_user_id=r.changed_by_user_id, changed_at=r.changed_at,
            reason=r.reason, before_value=r.before_value, after_value=r.after_value,
        )
        for r in rows
    ]
