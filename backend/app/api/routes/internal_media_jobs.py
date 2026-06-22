"""Internal media-generation job endpoints (Sprint 2A) — service-to-service.

chat-edge's ``generate_video`` tool calls these with
``X-Internal-Service-Secret`` (``auth.internal_trusted``); public callers
never reach them.

  POST /internal/v1/jobs/video        — submit a video job (DLP + balance gated)
  GET  /internal/v1/jobs/{id}         — poll status (USD siblings)
  GET  /internal/v1/jobs/{id}/output  — stream the generated artifact bytes

Sprint 2A.5: the submit flow now delegates to the shared
``media_gen.submit_helpers`` (DRY with the public direct-dispatch routes).
Behavior is unchanged for the internal contract; the only edge difference
is that a totally-unknown model now returns 404 (was 400) via the shared
``validate_model_capability`` — untested and an improvement.
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
from app.schemas.media_gen import (
    JobStatusResponse,
    VideoGenSubmitRequest,
    VideoGenSubmitResponse,
)
from app.services.media_gen.submit_helpers import (
    estimate_and_validate_cost,
    format_submit_response_usd,
    insert_generation_job_row,
    scan_media_prompt_with_profile,
    validate_model_capability,
)
from app.services.portal.credit_display import credits_to_usd_display

router = APIRouter(prefix="/internal/v1/jobs", tags=["internal-media"])


def _require_internal(auth: ResolvedApiKey) -> None:
    if not getattr(auth, "internal_trusted", False):
        raise HTTPException(status_code=401, detail="internal service auth required")


@router.post("/video", response_model=VideoGenSubmitResponse)
async def submit_video_job(
    payload: VideoGenSubmitRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> VideoGenSubmitResponse:
    _require_internal(auth)
    if not settings.video_gen_enabled:
        raise HTTPException(status_code=403, detail="video generation is disabled")

    user_id = getattr(auth, "portal_user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=400, detail="no end-user identity (X-User-ID required)"
        )

    mc = await validate_model_capability(session, payload.model, "supports_video_gen")
    await scan_media_prompt_with_profile(payload.prompt, session)

    meta = {
        "duration_sec": payload.duration_sec,
        "resolution": payload.resolution,
        "has_audio": payload.has_audio,
    }
    est = await estimate_and_validate_cost(
        session, auth.tenant_id, user_id, "video", mc, meta
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
        message_id=payload.message_id,
    )
    d = format_submit_response_usd(job, est.balance)
    d.pop("poll_url", None)  # internal response has no poll_url
    return VideoGenSubmitResponse(**d)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> JobStatusResponse:
    _require_internal(auth)
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")

    output_url = None
    meta = job.output_metadata or {}
    if job.status == "done" and meta.get("output_path"):
        output_url = f"/internal/v1/jobs/{job.id}/output"

    actual = job.actual_cost_credits
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress_pct=job.progress_pct,
        output_file_id=job.output_file_id,
        output_url=output_url,
        actual_cost_credits=actual,
        actual_cost_usd=credits_to_usd_display(actual) if actual else None,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/output")
async def get_job_output(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
):
    _require_internal(auth)
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
        filename=f"{job_id}.mp4",
    )
