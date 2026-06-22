"""ElevenLabs TTS adapter — synchronous fast-path (Sprint 4).

ElevenLabs ``POST /v1/text-to-speech/{voice_id}`` returns audio bytes inline,
so this mirrors the OpenAI TTS sync pattern: a ``SubmitResult`` with a
populated ``sync_result`` (URL-based, bytes stashed on the instance under a
``sync://`` marker for the job loop to fetch back). 2-arg ABC ``submit_job``;
model id / voice come from ``input_metadata``. The key is resolved per request
and never stored on the instance.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

import httpx

from app.core.exceptions import ProviderKeyMissing
from app.services.media_gen.adapters.replicate_base import ProviderAuthError
from app.services.media_gen.base import (
    MediaGenAdapter,
    PollResult,
    SubmitResult,
    SyncResult,
)
from app.services.media_gen.key_resolver import resolve_elevenlabs_key
from app.services.media_gen.spend_guard import (
    SpendCapExceeded,
    check_and_reserve_session_budget,
    release_session_budget,
)

_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
_DEFAULT_MODEL = "eleven_multilingual_v2"
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"


class ElevenLabsTTSAdapter(MediaGenAdapter):
    provider_name = "elevenlabs"
    # Canonical job_type is "audio_tts" (matches the route + generation_jobs).
    supported_job_types = ["audio_tts"]

    def __init__(
        self,
        key_resolver: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    ) -> None:
        self._resolve_key: Callable[[], Awaitable[Optional[str]]] = (
            key_resolver or resolve_elevenlabs_key
        )
        self._sync_bytes: dict[str, bytes] = {}

    def estimate_cost_usd(self, input_metadata: dict, model_config: Any) -> Decimal:
        char_count = len((input_metadata or {}).get("text", "") or "")
        per_char = Decimal(
            str(getattr(model_config, "cost_per_character", None) or "0.0001")
        )
        return (per_char * char_count).quantize(Decimal("0.0001"))

    async def submit_job(
        self, input_prompt: str, input_metadata: dict
    ) -> SubmitResult:
        meta = input_metadata or {}
        estimate = Decimal(str(meta.get("estimate_usd", "0") or "0"))
        if estimate < 0:
            raise SpendCapExceeded("negative cost estimate")
        if not await check_and_reserve_session_budget(estimate):
            raise SpendCapExceeded(f"session budget exceeded (estimate ${estimate})")

        try:
            key = await self._resolve_key()
            if not key:
                raise ProviderKeyMissing("No active ElevenLabs key")

            text = meta.get("text") or input_prompt
            voice_id = meta.get("voice_id", _DEFAULT_VOICE_ID)
            model = meta.get("tts_model") or _DEFAULT_MODEL
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{_TTS_BASE}/{voice_id}",
                    headers={"xi-api-key": key, "Content-Type": "application/json"},
                    json={"text": text, "model_id": model},
                )
            if resp.status_code == 401:
                raise ProviderAuthError("ElevenLabs auth failed")
            resp.raise_for_status()

            marker = f"sync://{uuid.uuid4().hex}"
            self._sync_bytes[marker] = resp.content
            return SubmitResult(
                external_job_id=None,
                initial_status="done",
                sync_result=SyncResult(
                    result_url=marker,
                    result_metadata={
                        "content_type": "audio/mpeg",
                        "voice_id": voice_id,
                        "char_count": len(text),
                    },
                ),
            )
        except Exception:
            await release_session_budget(estimate)
            raise

    async def poll_job(self, external_job_id: str) -> PollResult:
        raise NotImplementedError("ElevenLabs TTS is synchronous; poll not supported")

    async def fetch_result_bytes(self, result_url: str) -> bytes:
        data = self._sync_bytes.pop(result_url, None)
        if data is None:
            raise ValueError("no synchronous TTS bytes for marker")
        return data
