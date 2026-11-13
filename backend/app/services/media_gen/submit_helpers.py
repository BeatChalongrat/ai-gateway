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
    if capability_attr == "supports_video_gen":
        from app.services.media_gen.registry import registered_adapters
        if mc.provider == "openai" and model_id.startswith("sora-"):
            raise HTTPException(status_code=410, detail="Sora 2 / Videos API retired on 2026-09-24; configure a supported video provider.")
        if not mc.is_active or (mc.provider, "video") not in registered_adapters():
            raise HTTPException(status_code=400, detail="Video model is inactive or has no installed video adapter.")
        if mc.provider != "mock" and not (settings.video_gen_enabled and settings.video_model_verified):
            raise HTTPException(status_code=403, detail="Video generation requires the deployment gate and an owner-verified model.")
    if not getattr(mc, capability_attr, False):
        raise HTTPException(
            status_code=400,
            detail=f"model {model_id!r} does not support {capability_attr}",
        )
    return mc


async def scan_media_prompt_with_profile(
    prompt: str,
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    project_id: str = "",
    user_id: str | None = None,
) -> None:
    """MEDIA_GEN DLP scan; raise 403 on BLOCK (secret/credential).

    Identity-class PII PASSES through (MEDIA_GEN profile). No-op when DLP
    is disabled.

    Phase 9: when ``tenant_id`` is provided, the tenant's
    ``media_pii_egress`` policy applies — ``block`` fails closed on ANY
    identity PII (403); the default ``allow`` keeps the pass-through
    behavior byte-for-byte but writes a ``raw_egress`` audit row when
    identity PII actually egresses raw.
    """
    if not settings.dlp_enabled:
        return
    from app.services.dlp.egress_policy import (
        assert_egress_allowed,
        audit_raw_egress,
    )

    assert_egress_allowed("media_gen")
    orch = GatewayOrchestrator()
    profile = await get_runtime_profile_async(db, "media_gen")

    # Phase 10 (A2): resolve the tenant's egress policy BEFORE the scan —
    # a "block" tenant promises fail-closed on ANY identity PII, and names
    # are NER-only findings, so the block path must scan with NER enabled.
    # The default "allow" path (and the legacy no-tenant path) keeps the
    # cheap regex-only scan (only the credential class matters there).
    tenant = None
    if tenant_id is not None:
        from app.models.tenant import TenantORM

        tenant = await db.get(TenantORM, tenant_id)
    policy_blocks = tenant is not None and str(
        getattr(tenant, "media_pii_egress", "allow") or "allow"
    ) == "block"

    detection = await orch._scan_text_as_result_async(
        prompt, direction="input", ner_enabled=policy_blocks
    )
    enforcement = orch._scan_and_evaluate(detection, profile)
    if enforcement.is_blocked:
        raise HTTPException(status_code=403, detail="prompt blocked by DLP policy")
    if content_policy_blocked(prompt, profile):
        raise HTTPException(status_code=403, detail="prompt blocked by content policy")

    if not enforcement.match_actions or tenant_id is None:
        return

    if policy_blocks:
        raise HTTPException(
            status_code=403,
            detail=(
                "prompt contains personal data and this tenant's policy "
                "blocks unmasked media-generation egress"
            ),
        )
    await audit_raw_egress(
        db,
        channel="media_gen",
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        detection=detection,
    )


def content_policy_blocked(prompt: str, profile) -> bool:
    """Whether the content-policy detectors fire AND the active profile BLOCKs
    the ``content_policy`` category (Sprint 4).

    The detectors (CSAM / copyright / public figure) run ONLY on the media-gen
    path — they are deliberately NOT in the default DLP registry, so the chat
    path is unaffected. Returns a bool so each caller raises in its own idiom
    (the video/audio scan raises 403; the image route persists an audit row
    then raises DLPViolationError).
    """
    from app.domain.policy import EnforcementAction

    if profile.category_actions.get("content_policy") != EnforcementAction.BLOCK:
        return False
    from app.services.dlp.detectors.content_policy import content_policy_detectors

    for detector in content_policy_detectors():
        if detector.detect(prompt or ""):
            return True
    return False


async def estimate_and_validate_cost(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    job_type: str,
    model_config: ModelConfigORM,
    input_metadata: dict,
) -> EstimateResult:
    """Estimate USD cost + check balance; raise 402 if insufficient."""
    if job_type == "video":
        duration = input_metadata.get("duration_sec", 5)
        limit = max(1, min(60, settings.replicate_max_video_duration_sec))
        if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= limit:
            raise HTTPException(status_code=422, detail=f"duration_sec must be 1–{limit}; it is never silently shortened.")
    est = await estimate_and_check_balance(
        db, tenant_id, user_id, job_type, model_config, input_metadata
    )
    if not est.sufficient:
        raise HTTPException(status_code=402, detail="insufficient credits")
    return est


async def enforce_daily_cap_and_enrich_meta(
    db: AsyncSession,
    *,
    tenant_id: str,
    model_id: str,
    input_metadata: dict,
    est: EstimateResult,
) -> None:
    """L2 spend guard + adapter-metadata enrichment (Sprint 2B).

    Runs AFTER ``estimate_and_validate_cost`` and BEFORE the INSERT, for
    both the public and internal submit paths:

    - **L2 daily cap**: rejects with 402 when ``today's tenant spend +
      this estimate`` would exceed ``tenants.max_daily_spend_usd``. Mock
      jobs cost ~$0 so they always pass — no special-casing.
    - **Meta enrichment**: stuffs ``estimate_usd`` (for the adapter's L1
      reservation) and ``replicate_model`` (the model_configs.model path)
      into ``input_metadata`` IN PLACE, because the ``MediaGenAdapter``
      ABC ``submit_job(prompt, meta)`` signature carries no model_config.
      Harmless for the mock adapter (it ignores them).
    """
    cost_usd = Decimal(str((est.breakdown or {}).get("cost_usd", 0) or 0))

    # Local import keeps the spend_guard (Redis) dependency off the hot
    # import path for routes that never touch media gen.
    from app.services.media_gen.spend_guard import check_daily_spend_cap

    # L2 fails OPEN on a Redis/infra error: the L1 atomic reserve (in the
    # adapter's submit_job, at provider-call time) is the HARD backstop for
    # real money, and it itself fails closed if Redis is down. So a transient
    # Redis blip here must not 500 the submit (and must not block the free
    # mock provider, which has no L1 reservation at all).
    cap = None
    try:
        cap = await check_daily_spend_cap(db, tenant_id, cost_usd)
    except Exception:  # noqa: BLE001
        cap = None
    if cap is not None and not cap.allowed:
        raise HTTPException(
            status_code=402,
            detail=(
                f"daily spend cap reached: today ${cap.current_spend} + "
                f"${cost_usd} would exceed the ${cap.cap} tenant limit"
            ),
        )

    input_metadata["estimate_usd"] = str(cost_usd)
    input_metadata["replicate_model"] = model_id


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
            if existing.user_id != user_id:
                raise HTTPException(status_code=409, detail="Idempotency key is already in use")
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
        status=("pending_confirm" if job_type == "video" and credits_to_usd(estimate_credits) >= Decimal(str(settings.video_gen_default_threshold_usd)) else "queued"),
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
        "requires_confirmation": job.status == "pending_confirm" if job.job_type == "video" else credits_to_usd(est) >= threshold,
        "poll_url": f"/v1/jobs/{job.id}",
    }
