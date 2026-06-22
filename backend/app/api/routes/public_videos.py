"""Public direct-dispatch video generation (Sprint 2A.5).

The "pick a video model → fill a form → submit" entry point, parallel to
the chat-edge tool path. Same backend infrastructure (generation_jobs,
worker loop, adapters); only the entry point differs. Public auth (API
key / session), NOT the internal HMAC gate.

  POST /v1/videos/generations  — submit a video job
  GET  /v1/jobs/{job_id}        — poll status (USD siblings) [shared, any modality]
  GET  /v1/jobs/{job_id}/output — stream the generated artifact bytes
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.db import get_async_db
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.schemas.media_gen_public import (
    JobStatusPublicResponse,
    VideoGenPublicRequest,
    VideoGenPublicResponse,
)
from app.services.media_gen.submit_helpers import (
    enforce_daily_cap_and_enrich_meta,
    estimate_and_validate_cost,
    format_submit_response_usd,
    insert_generation_job_row,
    scan_media_prompt_with_profile,
    validate_model_capability,
)
from app.services.portal.credit_display import credits_to_usd_display

router = APIRouter(tags=["public-media"])

# Hard cap on video duration (Sprint 2B introduces a Replicate-specific
# setting; until then a sane default).
_DEFAULT_MAX_VIDEO_SEC = 10


def _max_video_seconds() -> int:
    return int(getattr(settings, "replicate_max_video_duration_sec", _DEFAULT_MAX_VIDEO_SEC))


async def _resolve_user(auth: ResolvedApiKey, session: AsyncSession) -> tuple[str, int]:
    user_id = getattr(auth, "portal_user_id", None)
    if not user_id:
        raise HTTPException(status_code=400, detail="no end-user identity")
    user = await session.get(PortalUserORM, user_id)
    balance = int(user.credit_balance or 0) if user is not None else 0
    return user_id, balance


@router.post("/videos/generations", response_model=VideoGenPublicResponse)
async def generate_video(
    payload: VideoGenPublicRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> VideoGenPublicResponse:
    if not settings.video_gen_enabled:
        raise HTTPException(status_code=403, detail="video generation is disabled")
    user_id, _balance = await _resolve_user(auth, session)

    mc = await validate_model_capability(session, payload.model, "supports_video_gen")
    await scan_media_prompt_with_profile(payload.prompt, session)

    duration = max(1, min(int(payload.duration_sec or 5), _max_video_seconds()))
    meta = {
        "duration_sec": duration,
        "resolution": payload.resolution,
        "has_audio": payload.has_audio,
    }
    est = await estimate_and_validate_cost(
        session, auth.tenant_id, user_id, "video", mc, meta
    )
    # L2 daily-spend cap + enrich meta with estimate_usd/replicate_model.
    await enforce_daily_cap_and_enrich_meta(
        session, tenant_id=auth.tenant_id, model_id=payload.model,
        input_metadata=meta, est=est,
    )
    job = await insert_generation_job_row(
        session,
        tenant_id=auth.tenant_id,
        user_id=user_id,
        model_id=payload.model,
        job_type="video",
        provider=mc.provider,
        input_prompt=payload.prompt,
        input_metadata=meta,
        estimate_credits=est.estimated_credits,
        idempotency_key=payload.idempotency_key,
        conversation_id=payload.conversation_id,
    )
    return VideoGenPublicResponse(**format_submit_response_usd(job, est.balance))


@router.get("/jobs/{job_id}", response_model=JobStatusPublicResponse)
async def get_job_status(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> JobStatusPublicResponse:
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")

    output_url = None
    meta = job.output_metadata or {}
    if job.status == "done" and meta.get("output_path"):
        output_url = f"/v1/jobs/{job.id}/output"

    actual = job.actual_cost_credits
    return JobStatusPublicResponse(
        job_id=job.id,
        status=job.status,
        progress_pct=job.progress_pct,
        output_url=output_url,
        actual_cost_credits=actual,
        actual_cost_usd=credits_to_usd_display(actual) if actual else None,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobStatusPublicResponse)
async def cancel_job(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> JobStatusPublicResponse:
    """Cancel a reserved / in-flight job (Sprint 3).

    Idempotent: a job already in a terminal state is returned unchanged. For
    an active (``queued``/``processing``) job we mark it ``cancelled``, refund
    the L1 session reservation, and best-effort cancel the provider job. The
    job loop's webhook-aware re-read stops polling once it sees ``cancelled``.
    """
    from datetime import datetime, timezone
    from decimal import Decimal

    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")

    if job.status in ("queued", "processing"):
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(job)

        # Refund the L1 session reservation (best-effort; the estimate was
        # stashed on input_metadata at submit time — Sprint 2B.5).
        est = (job.input_metadata or {}).get("estimate_usd")
        if est:
            try:
                from app.services.media_gen.spend_guard import release_session_budget

                await release_session_budget(Decimal(str(est)))
            except Exception:  # noqa: BLE001 — refund is best-effort
                pass

        # Best-effort provider-side cancel for an already-dispatched job.
        if job.external_job_id:
            try:
                from app.services.media_gen.registry import get_adapter

                adapter = get_adapter(job.provider, job.job_type)
                await adapter.cancel_job(job.external_job_id)
            except Exception:  # noqa: BLE001 — provider cancel is best-effort
                pass

    actual = job.actual_cost_credits
    return JobStatusPublicResponse(
        job_id=job.id,
        status=job.status,
        progress_pct=job.progress_pct,
        output_url=None,
        actual_cost_credits=actual,
        actual_cost_usd=credits_to_usd_display(actual) if actual else None,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/jobs/{job_id}/output")
async def get_job_output(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
):
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")
    meta = job.output_metadata or {}
    path = meta.get("output_path")
    if job.status != "done" or not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no output available")
    return FileResponse(
        path,
        media_type=meta.get("content_type", "video/mp4"),
        filename=f"{job_id}.{ 'mp4' if str(meta.get('content_type','')).startswith('video') else 'bin'}",
    )
