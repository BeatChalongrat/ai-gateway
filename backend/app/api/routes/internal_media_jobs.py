"""Internal media-generation job endpoints (Sprint 2A) — service-to-service.

chat-edge's ``generate_video`` tool calls these with
``X-Internal-Service-Secret`` (``auth.internal_trusted``); public callers
never reach them.

  POST /internal/v1/jobs/video        — submit a video job (DLP + balance gated)
  GET  /internal/v1/jobs/{id}         — poll status (USD siblings)
  GET  /internal/v1/jobs/{id}/output  — stream the generated artifact bytes
                                        (chat-edge fetches this and persists
                                        the ConversationFile on its side)

Every credit field in a response carries a sibling ``*_usd`` display
string. DLP uses the MEDIA_GEN profile (secret/credential BLOCK, identity
PASS-through).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.db import get_async_db
from app.models.generation_job import GenerationJobORM
from app.models.model_config import ModelConfigORM
from app.models.portal_user import PortalUserORM
from app.schemas.media_gen import (
    JobStatusResponse,
    VideoGenSubmitRequest,
    VideoGenSubmitResponse,
)
from app.services.gateway.portal_credit_check import estimate_and_check_balance
from app.services.orchestrator import GatewayOrchestrator
from app.services.policy.profile_service import get_runtime_profile_async
from app.services.portal.credit_display import (
    credits_to_usd,
    credits_to_usd_display,
)

router = APIRouter(prefix="/internal/v1/jobs", tags=["internal-media"])


def _require_internal(auth: ResolvedApiKey) -> None:
    if not getattr(auth, "internal_trusted", False):
        raise HTTPException(status_code=401, detail="internal service auth required")


def _submit_response(job: GenerationJobORM, balance: int) -> VideoGenSubmitResponse:
    est = int(job.estimated_cost_credits)
    threshold = Decimal(str(settings.video_gen_default_threshold_usd))
    balance_after = max(0, balance - est)
    return VideoGenSubmitResponse(
        job_id=job.id,
        status=job.status,
        estimated_cost_credits=est,
        estimated_cost_usd=credits_to_usd_display(est),
        balance_after_credits=balance_after,
        balance_after_usd=credits_to_usd_display(balance_after),
        requires_confirmation=credits_to_usd(est) >= threshold,
    )


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

    user = await session.get(PortalUserORM, user_id)
    balance = int(user.credit_balance or 0) if user is not None else 0

    # Model + capability gate.
    mc = (
        await session.execute(
            select(ModelConfigORM).where(ModelConfigORM.model == payload.model)
        )
    ).scalars().first()
    if mc is None or not mc.supports_video_gen:
        raise HTTPException(
            status_code=400, detail="model does not support video generation"
        )

    # Idempotency: a repeated key returns the existing job (no new row).
    if payload.idempotency_key:
        existing = (
            await session.execute(
                select(GenerationJobORM).where(
                    GenerationJobORM.tenant_id == auth.tenant_id,
                    GenerationJobORM.idempotency_key == payload.idempotency_key,
                )
            )
        ).scalars().first()
        if existing is not None:
            return _submit_response(existing, balance)

    # DLP — MEDIA_GEN profile: secret/credential BLOCK, identity PASS.
    if settings.dlp_enabled:
        orch = GatewayOrchestrator()
        profile = await get_runtime_profile_async(session, "media_gen")
        detection = await orch._scan_text_as_result_async(
            payload.prompt, direction="input", ner_enabled=False
        )
        enforcement = orch._scan_and_evaluate(detection, profile)
        if enforcement.is_blocked:
            raise HTTPException(
                status_code=403, detail="prompt blocked by DLP policy"
            )

    meta = {
        "duration_sec": payload.duration_sec,
        "resolution": payload.resolution,
        "has_audio": payload.has_audio,
    }
    est = await estimate_and_check_balance(
        session, auth.tenant_id, user_id, "video", mc, meta
    )
    if not est.sufficient:
        raise HTTPException(status_code=402, detail="insufficient credits")

    job = GenerationJobORM(
        id=str(uuid.uuid4()),
        tenant_id=auth.tenant_id,
        user_id=user_id,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        job_type="video",
        provider=mc.provider,
        model_id=payload.model,
        status="queued",
        input_prompt=payload.prompt,
        input_metadata=meta,
        estimated_cost_credits=est.estimated_credits,
        idempotency_key=payload.idempotency_key,
        ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return _submit_response(job, balance)


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
