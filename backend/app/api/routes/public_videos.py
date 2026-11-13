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

from sqlalchemy import func, select, update

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.db import get_async_db
from app.models.generation_job import GenerationJobORM
from app.models.model_config import ModelConfigORM
from app.models.portal_user import PortalUserORM
from app.schemas.media_gen_public import (
    JobStatusPublicResponse,
    VideoGenPublicRequest,
    VideoGenPublicResponse,
)
from app.services.media_gen.rate_limit import enforce_media_rate_limit
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
    return max(1, min(60, int(getattr(settings, "replicate_max_video_duration_sec", _DEFAULT_MAX_VIDEO_SEC))))


async def _resolve_video_capability(session: AsyncSession) -> dict:
    """Gate + DB key + owner verification + an active installed video adapter.

    This checks configuration, not live provider entitlement or model liveness.
    """
    gate = bool(settings.video_gen_enabled)
    from app.services.provider_key_service import ProviderKeyService
    from app.services.media_gen.registry import registered_adapters
    # Use the same DB-backed key source as the adapter, not the retired env key.
    key_configured = bool(await ProviderKeyService().get_active_key_async(session, "replicate"))
    video_providers = [p for p, kind in registered_adapters() if kind == "video" and p != "mock"]
    model_verified = bool(settings.video_model_verified)
    model_count = 0
    if gate:
        model_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ModelConfigORM)
                    .where(
                        ModelConfigORM.supports_video_gen.is_(True),
                        ModelConfigORM.is_active.is_(True),
                        ModelConfigORM.provider.in_(video_providers),
                    )
                )
            ).scalar()
            or 0
        )
    enabled = gate and key_configured and model_verified and model_count > 0
    if enabled:
        reason = ""
    elif not gate:
        reason = "Video generation is turned off for this deployment."
    elif not key_configured:
        reason = "No video provider key is configured."
    elif model_count == 0:
        reason = "No video model is available on this deployment."
    else:  # key + gate + models present, but the model has not been verified
        reason = (
            "The video model has not been verified as current — an owner must "
            "confirm the configured model is still available before video is enabled."
        )
    return {
        "enabled": enabled,
        "video_gen_enabled": gate,
        "key_configured": key_configured,
        "model_verified": model_verified,
        "model_count": model_count,
        "reason": reason,
        "supported_controls": {"duration_sec": [1, _max_video_seconds()], "resolution": ["auto"], "has_audio": [False]},
        "reference_image_supported": False,
    }


@router.get(
    "/video/config",
    summary="Whether the video-generation button should be offered",
    description=(
        "Capability answer for the 🎬 button, parallel to GET /v1/search/config "
        "for web_search. `enabled` is true only when the gate, a configured key, "
        "and an owner-verified model all agree. Both clients gate the button on "
        "`enabled` and show `reason` when false, so the control is visibly "
        "unavailable — never a dead button that does nothing (FIX9 §B3.5)."
    ),
)
async def video_config(
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    """Report whether video generation is available for this caller."""
    return await _resolve_video_capability(session)


async def _resolve_user(auth: ResolvedApiKey, session: AsyncSession) -> tuple[str, int]:
    user_id = getattr(auth, "portal_user_id", None)
    if not user_id:
        raise HTTPException(status_code=400, detail="no end-user identity")
    user = await session.get(PortalUserORM, user_id)
    balance = int(user.credit_balance or 0) if user is not None else 0
    return user_id, balance


@router.post(
    "/videos/generations",
    response_model=VideoGenPublicResponse,
    summary="Submit a video generation job",
    description=(
        "Submit a video job (API key or session auth). Returns a queued job "
        "immediately; poll `poll_url` for completion. Rate limit: 5/hour per "
        "key. Errors: 400 DLP/content-policy block or non-video model, 403 "
        "feature disabled, 404 model not found, 429 rate limit."
    ),
)
async def generate_video(
    payload: VideoGenPublicRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> VideoGenPublicResponse:
    """Submit a video generation job and return its queued status + cost."""
    # FIX11 §1: gate the submit on the SAME deployment-level signals the button
    # reads (gate AND a configured key AND an owner-verified model), so a live
    # button and its submit can never disagree. The SPECIFIC requested model is
    # validated just below (404 unknown / 400 non-video) — model_count is a
    # button-offer concern (GET /v1/video/config), not the per-request gate, so
    # those precise errors survive.
    _cap = await _resolve_video_capability(session)
    if not (_cap["video_gen_enabled"] and _cap["key_configured"] and _cap["model_verified"]):
        raise HTTPException(status_code=403, detail=_cap["reason"] or "video generation is unavailable")
    await enforce_media_rate_limit(
        auth, bucket="video", limit=settings.media_rate_limit_video_per_hour,
        window_seconds=3600,
        scope=f"{settings.media_rate_limit_video_per_hour}/hour per key",
    )
    user_id, _balance = await _resolve_user(auth, session)

    mc = await validate_model_capability(session, payload.model, "supports_video_gen")
    await scan_media_prompt_with_profile(
        payload.prompt, session,
        tenant_id=auth.tenant_id,
        project_id=str(getattr(auth, "project_id", "") or ""),
        user_id=user_id,
    )

    duration = payload.duration_sec
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


@router.get("/jobs")
async def list_conversation_video_jobs(
    conversation_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    from uuid import UUID
    try:
        conversation_id = str(UUID(conversation_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid conversation id")
    user_id, _ = await _resolve_user(auth, session)
    await enforce_media_rate_limit(auth, bucket="jobs", limit=settings.media_rate_limit_jobs_per_min,
                                   window_seconds=60, scope="jobs/min per key")
    rows = (await session.execute(select(GenerationJobORM).where(
        GenerationJobORM.tenant_id == auth.tenant_id, GenerationJobORM.user_id == user_id,
        GenerationJobORM.conversation_id == conversation_id, GenerationJobORM.job_type == "video",
    ).order_by(GenerationJobORM.created_at.desc()).limit(20))).scalars().all()
    return {"jobs": [{"job_id": j.id, "status": j.status, "model": j.model_id,
                     "progress_pct": j.progress_pct, "created_at": j.created_at,
                     "estimated_cost_usd": credits_to_usd_display(j.estimated_cost_credits),
                     "error_message": j.error_message,
                     "output_url": f"/v1/jobs/{j.id}/output" if j.status == "done" else None}
                    for j in rows]}


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusPublicResponse,
    summary="Poll a generation job's status",
    description=(
        "Shared across all modalities. Returns status, progress, the derived "
        "`output_url` and provider-disclosed `provenance` (once done), and the "
        "actual cost. Rate limit: 60/min per key. 404 if the job belongs to "
        "another tenant."
    ),
)
async def get_job_status(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> JobStatusPublicResponse:
    """Return the current status, output URL, provenance, and cost of a job."""
    await enforce_media_rate_limit(
        auth, bucket="jobs", limit=settings.media_rate_limit_jobs_per_min,
        window_seconds=60,
        scope=f"{settings.media_rate_limit_jobs_per_min}/min per key",
    )
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id or job.user_id != getattr(auth, "portal_user_id", None):
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
        provenance=meta.get("provenance"),
        actual_cost_credits=actual,
        actual_cost_usd=credits_to_usd_display(actual) if actual else None,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/jobs/{job_id}/confirm", response_model=JobStatusPublicResponse)
async def confirm_video_job(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> JobStatusPublicResponse:
    """Owner-only, idempotent transition. Workers claim queued jobs only."""
    from datetime import datetime, timezone
    await enforce_media_rate_limit(auth, bucket="confirm", limit=settings.media_rate_limit_cancel_per_min,
                                   window_seconds=60, scope="video confirmations/min per key")
    user_id, balance = await _resolve_user(auth, session)
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id or job.user_id != user_id or job.job_type != "video":
        raise HTTPException(status_code=404, detail="job not found")
    if job.status == "pending_confirm":
        expiry = job.ttl_expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Video quote expired; submit a new request.")
        mc = await validate_model_capability(session, job.model_id, "supports_video_gen")
        if balance < job.estimated_cost_credits:
            raise HTTPException(status_code=402, detail="insufficient credits")
        estimate = await estimate_and_validate_cost(session, auth.tenant_id, user_id, "video", mc, job.input_metadata or {})
        if estimate.estimated_credits > job.estimated_cost_credits:
            raise HTTPException(status_code=409, detail="Video price changed; submit a new request for a current quote.")
        # Conditional UPDATE cannot revive a concurrently cancelled job.
        await session.execute(update(GenerationJobORM).where(
            GenerationJobORM.id == job.id,
            GenerationJobORM.status == "pending_confirm",
        ).values(status="queued"))
        await session.commit()
    return await get_job_status(job_id, auth, session)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobStatusPublicResponse,
    summary="Cancel a reserved / in-flight job",
    description=(
        "Idempotent — a terminal job is returned unchanged. Refunds the L1 "
        "session reservation and best-effort cancels the provider job. Rate "
        "limit: 10/min per key."
    ),
)
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

    await enforce_media_rate_limit(
        auth, bucket="cancel", limit=settings.media_rate_limit_cancel_per_min,
        window_seconds=60,
        scope=f"{settings.media_rate_limit_cancel_per_min}/min per key",
    )
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id or job.user_id != getattr(auth, "portal_user_id", None):
        raise HTTPException(status_code=404, detail="job not found")

    if job.status in ("pending_confirm", "queued", "processing"):
        was_processing = job.status == "processing"
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(job)

        # Refund the L1 session reservation (best-effort; the estimate was
        # stashed on input_metadata at submit time — Sprint 2B.5).
        est = (job.input_metadata or {}).get("estimate_usd") if was_processing else None
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


@router.get(
    "/jobs/{job_id}/output",
    summary="Stream the generated artifact bytes",
    description=(
        "Streams the persisted artifact (FileResponse). 404 until the job is "
        "done. Bytes are stored server-side so a direct API caller can refetch "
        "without a chat-edge conversation."
    ),
)
async def get_job_output(
    job_id: str,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
):
    job = await session.get(GenerationJobORM, job_id)
    if job is None or job.tenant_id != auth.tenant_id or job.user_id != getattr(auth, "portal_user_id", None):
        raise HTTPException(status_code=404, detail="job not found")
    meta = job.output_metadata or {}
    path = meta.get("output_path")
    if job.status != "done" or not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no output available")
    # Phase 10 (A1): the persisted artifact carries the correct extension
    # for its modality (mp4/mp3/wav/…) — surface that name on download.
    return FileResponse(
        path,
        media_type=meta.get("content_type", "application/octet-stream"),
        filename=os.path.basename(path),
    )
