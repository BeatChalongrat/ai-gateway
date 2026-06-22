"""Public direct-dispatch audio generation — TTS + music (Sprint 2A.5).

Same flow as public_videos but branched by ``mode``:
  - "tts"   → validate supports_tts, cost via cost_per_character × len(text)
  - "music" → validate supports_audio_gen, cost via cost_per_generation

  POST /v1/audio/generations
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.db import get_async_db
from app.models.portal_user import PortalUserORM
from app.schemas.media_gen_public import (
    AudioGenPublicRequest,
    AudioGenPublicResponse,
)
from app.services.media_gen.submit_helpers import (
    estimate_and_validate_cost,
    format_submit_response_usd,
    insert_generation_job_row,
    scan_media_prompt_with_profile,
    validate_model_capability,
)

router = APIRouter(tags=["public-media"])


@router.post("/audio/generations", response_model=AudioGenPublicResponse)
async def generate_audio(
    payload: AudioGenPublicRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> AudioGenPublicResponse:
    if not settings.video_gen_enabled:
        # Media generation shares one feature flag for the alpha.
        raise HTTPException(status_code=403, detail="media generation is disabled")

    user_id = getattr(auth, "portal_user_id", None)
    if not user_id:
        raise HTTPException(status_code=400, detail="no end-user identity")

    mode = (payload.mode or "").lower()
    if mode == "tts":
        mc = await validate_model_capability(session, payload.model, "supports_tts")
        job_type = "audio_tts"
        meta = {"char_count": len(payload.prompt or ""), "voice_id": payload.voice_id}
    elif mode == "music":
        mc = await validate_model_capability(
            session, payload.model, "supports_audio_gen"
        )
        job_type = "music"
        meta = {"duration_sec": payload.duration_sec or 30}
    else:
        raise HTTPException(status_code=400, detail="mode must be 'tts' or 'music'")

    await scan_media_prompt_with_profile(payload.prompt, session)

    user = await session.get(PortalUserORM, user_id)
    _balance = int(user.credit_balance or 0) if user is not None else 0

    est = await estimate_and_validate_cost(
        session, auth.tenant_id, user_id, job_type, mc, meta
    )
    job = await insert_generation_job_row(
        session,
        tenant_id=auth.tenant_id,
        user_id=user_id,
        model_id=payload.model,
        job_type=job_type,
        provider=mc.provider,
        input_prompt=payload.prompt,
        input_metadata=meta,
        estimate_credits=est.estimated_credits,
        idempotency_key=payload.idempotency_key,
        conversation_id=payload.conversation_id,
    )
    return AudioGenPublicResponse(**format_submit_response_usd(job, est.balance))
