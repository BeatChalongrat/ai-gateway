"""OpenAI voice client — the gateway-side utility endpoint (FIX 2).

chat-edge used to call OpenAI Whisper (``/v1/audio/transcriptions``) and TTS
(``/v1/audio/speech``) directly, bypassing DLP. The gateway now fronts both,
and THIS module is the only place voice content leaves for OpenAI. It sits
BEHIND the DLP raw-egress gates:

  * TTS: ``scan_voice_text`` (credential-block + per-tenant PII + audit) runs
    on the text before :func:`synthesize`;
  * STT: ``assert_stt_egress_allowed`` (per-tenant block + audit) runs before
    :func:`transcribe` — audio cannot be scanned pre-transcription
    (documented limitation); the transcript's onward path is masked by the
    chat pipeline.

Classified ``raw_whitelisted`` in ``tests/test_dlp_egress_invariant.py`` (RAW
user content by design, gated by the whitelisted ``voice`` channel). Empty
``settings.openai_api_key`` disables both (503).
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError, ValidationError

logger = structlog.get_logger(__name__)

_STT_TIMEOUT_SECONDS = 90.0
_TTS_TIMEOUT_SECONDS = 90.0

# OpenAI's documented TTS voices; anything else is a 400 upstream.
ALLOWED_TTS_VOICES = frozenset(
    {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
)


def voice_enabled() -> bool:
    """Whether the gateway has the OpenAI credential to run voice."""
    return bool((settings.openai_api_key or "").strip())


def _base_url() -> str:
    return (settings.openai_utility_base_url or "https://api.openai.com/v1").rstrip("/")


async def transcribe(
    audio: bytes,
    *,
    filename: str,
    content_type: str,
    language: str | None = None,
    model: str | None = None,
) -> str:
    """Speech-to-text via Whisper. Returns the transcript string.

    The caller MUST have run ``assert_stt_egress_allowed`` first (the route
    does) — audio itself is not scannable here.
    """
    if not voice_enabled():
        raise ProviderUnavailableError(
            "voice provider not configured (set OPENAI_API_KEY)"
        )
    if not audio:
        raise ValidationError("audio is empty")

    mdl = model or settings.openai_whisper_model
    files = {"file": (filename or "audio.webm", audio, content_type or "audio/webm")}
    data = {"model": mdl}
    if language:
        data["language"] = language

    try:
        async with httpx.AsyncClient(timeout=_STT_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{_base_url()}/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                files=files,
                data=data,
            )
    except httpx.HTTPError as exc:
        logger.warning("voice_stt_upstream_unreachable", error=str(exc))
        raise ProviderUnavailableError("voice provider unreachable") from exc

    if resp.status_code != 200:
        logger.warning("voice_stt_upstream_error", status=resp.status_code)
        raise ProviderUnavailableError(
            f"voice provider returned {resp.status_code}"
        )
    return str((resp.json() or {}).get("text", ""))


async def synthesize(
    text: str,
    *,
    voice: str = "alloy",
    speed: float = 1.0,
    model: str | None = None,
) -> bytes:
    """Text-to-speech via TTS. Returns the mp3 audio bytes.

    The caller MUST have run ``scan_voice_text`` on ``text`` first (the route
    does). Voice/speed are validated here to avoid an upstream round-trip.
    """
    if not voice_enabled():
        raise ProviderUnavailableError(
            "voice provider not configured (set OPENAI_API_KEY)"
        )
    text = (text or "").strip()
    if not text:
        raise ValidationError("text is required")

    voice = (voice or "alloy").lower()
    if voice not in ALLOWED_TTS_VOICES:
        raise ValidationError("unknown voice")
    try:
        speed = float(speed or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.25, min(4.0, speed))

    mdl = model or settings.openai_tts_model
    try:
        async with httpx.AsyncClient(timeout=_TTS_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{_base_url()}/audio/speech",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": mdl,
                    "voice": voice,
                    "input": text,
                    "speed": speed,
                    "response_format": "mp3",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("voice_tts_upstream_unreachable", error=str(exc))
        raise ProviderUnavailableError("voice provider unreachable") from exc

    if resp.status_code != 200:
        logger.warning("voice_tts_upstream_error", status=resp.status_code)
        raise ProviderUnavailableError(
            f"voice provider returned {resp.status_code}"
        )
    return resp.content
