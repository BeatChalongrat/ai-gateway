"""Shared submit logic for media-generation routes (Sprint 2A.5).

DRY core used by BOTH the internal (chat-edge tool path,
``internal_media_jobs.py``) and the public direct-dispatch routes
(``public_videos.py`` / ``public_audio.py``): model + capability
validation, MEDIA_GEN DLP scan, cost estimate + balance gate, the single
``generation_jobs`` INSERT (with idempotency dedup), and the USD-sibling
response shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.generation_job import GenerationJobORM
from app.models.model_config import ModelConfigORM
from app.services.gateway.portal_credit_check import (
    EstimateResult,
    estimate_and_check_balance,
)
from app.services.orchestrator import GatewayOrchestrator
from app.services.policy.profile_service import get_runtime_profile_async
from app.services.portal.credit_display import (
    credits_to_usd,
    credits_to_usd_display,
)


async def validate_model_capability(
    db: AsyncSession, model_id: str, capability_attr: str
) -> ModelConfigORM:
    """Return the model_configs row for ``model_id`` or raise.

    404 when the model doesn't exist; 400 when it exists but lacks the
    required capability (e.g. ``supports_video_gen``).
    """
    mc = (
        await db.execute(
            select(ModelConfigORM).where(ModelConfigORM.model == model_id)
        )
    ).scalars().first()
    if mc is None:
        raise HTTPException(status_code=404, detail=f"model {model_id!r} not found")
    if not getattr(mc, capability_attr, False):
        raise HTTPException(
            status_code=400,
            detail=f"model {model_id!r} does not support {capability_attr}",
        )
    return mc


async def scan_media_prompt_with_profile(prompt: str, db: AsyncSession) -> None:
    """MEDIA_GEN DLP scan; raise 403 on BLOCK (secret/credential).

    Identity-class PII PASSES through (MEDIA_GEN profile). No-op when DLP
    is disabled.
    """
    if not settings.dlp_enabled:
        return
    orch = GatewayOrchestrator()
    profile = await get_runtime_profile_async(db, "media_gen")
    detection = await orch._scan_text_as_result_async(
        prompt, direction="input", ner_enabled=False
    )
    enforcement = orch._scan_and_evaluate(detection, profile)
    if enforcement.is_blocked:
        raise HTTPException(status_code=403, detail="prompt blocked by DLP policy")


async def estimate_and_validate_cost(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    job_type: str,
    model_config: ModelConfigORM,
    input_metadata: dict,
) -> EstimateResult:
    """Estimate USD cost + check balance; raise 402 if insufficient."""
    est = await estimate_and_check_balance(
        db, tenant_id, user_id, job_type, model_config, input_metadata
    )
    if not est.sufficient:
        raise HTTPException(status_code=402, detail="insufficient credits")
    return est


async def insert_generation_job_row(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    model_id: str,
    job_type: str,
    provider: str,
    input_prompt: str,
    input_metadata: dict,
    estimate_credits: int,
    idempotency_key: Optional[str] = None,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> GenerationJobORM:
    """The single ``generation_jobs`` INSERT path.

    When ``idempotency_key`` is set and a job already exists for
    (tenant, key), the existing row is returned (no new insert).
    """
    if idempotency_key:
        existing = (
            await db.execute(
                select(GenerationJobORM).where(
                    GenerationJobORM.tenant_id == tenant_id,
                    GenerationJobORM.idempotency_key == idempotency_key,
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing
    job = GenerationJobORM(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        job_type=job_type,
        provider=provider,
        model_id=model_id,
        status="queued",
        input_prompt=input_prompt,
        input_metadata=input_metadata,
        estimated_cost_credits=estimate_credits,
        idempotency_key=idempotency_key,
        ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def format_submit_response_usd(job: GenerationJobORM, balance: int) -> dict:
    """Common submit-response shape with USD siblings + poll_url.

    Routes pick the fields their response model needs (the internal
    response omits ``poll_url``).
    """
    est = int(job.estimated_cost_credits)
    threshold = Decimal(str(settings.video_gen_default_threshold_usd))
    balance_after = max(0, balance - est)
    return {
        "job_id": job.id,
        "status": job.status,
        "estimated_cost_credits": est,
        "estimated_cost_usd": credits_to_usd_display(est),
        "balance_after_credits": balance_after,
        "balance_after_usd": credits_to_usd_display(balance_after),
        "requires_confirmation": credits_to_usd(est) >= threshold,
        "poll_url": f"/v1/jobs/{job.id}",
    }
