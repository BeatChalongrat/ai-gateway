"""Voice endpoints — POST /v1/voice/transcribe + /v1/voice/synthesize (FIX 2).

The gateway now fronts voice so chat-edge stops calling OpenAI Whisper/TTS
directly (which bypassed DLP).

* ``/voice/transcribe`` (STT): auth -> tenant -> ``assert_stt_egress_allowed``
  (per-tenant block + audit; audio can't be scanned pre-transcription) ->
  Whisper -> ``{ "text": ... }``. The transcript's onward path re-enters the
  MASKED chat pipeline, where the credential class IS enforced.
* ``/voice/synthesize`` (TTS): auth -> tenant -> ``scan_voice_text``
  (credential-block + per-tenant PII + audit) on the text -> TTS -> mp3.

Same auth as chat completions (service JWT / API key + trusted end-user).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.db import get_async_db
from app.core.exceptions import ValidationError
from app.models.tenant import TenantORM
from app.services.dlp.egress_policy import assert_stt_egress_allowed, scan_voice_text
from app.services.voice.openai_voice_client import synthesize, transcribe

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["public-voice"])

# Whisper accepts up to 25 MB; cap the read to defend against a lying
# Content-Length. Mirrors the old chat-edge ceiling.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.post("/voice/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(...),
    language: str | None = Form(default=None),
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    tenant = await session.get(TenantORM, auth.tenant_id)

    # STT egress gate BEFORE reading/forwarding the audio: a "block" tenant
    # is refused (audio can't be PII-scanned); otherwise the raw-audio
    # egress is audit-flagged.
    await assert_stt_egress_allowed(
        session,
        tenant,
        project_id=str(getattr(auth, "project_id", "") or ""),
        user_id=getattr(auth, "portal_user_id", None),
    )

    data = await audio.read(_MAX_AUDIO_BYTES + 1)
    if len(data) > _MAX_AUDIO_BYTES:
        raise ValidationError("audio too large (max 25 MB)")

    text = await transcribe(
        data,
        filename=audio.filename or "audio.webm",
        content_type=audio.content_type or "audio/webm",
        language=language or None,
    )

    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — transcription already produced
        logger.warning("voice_stt_audit_commit_failed", error=str(exc))

    return {"text": text}


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)
    voice: str = "alloy"
    speed: float = 1.0


@router.post("/voice/synthesize")
async def voice_synthesize(
    payload: SynthesizeRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> Response:
    tenant = await session.get(TenantORM, auth.tenant_id)

    # DLP egress scan on the text-to-speak BEFORE the provider call.
    await scan_voice_text(
        payload.text,
        session,
        tenant,
        project_id=str(getattr(auth, "project_id", "") or ""),
        user_id=getattr(auth, "portal_user_id", None),
    )

    audio = await synthesize(payload.text, voice=payload.voice, speed=payload.speed)

    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — audio already produced
        logger.warning("voice_tts_audit_commit_failed", error=str(exc))

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
