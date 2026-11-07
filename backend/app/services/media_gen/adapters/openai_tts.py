"""OpenAI TTS adapter — synchronous fast-path (Sprint 4).

OpenAI's ``/v1/audio/speech`` returns the audio bytes inline, so there is no
async job to poll. This adapter returns a ``SubmitResult`` with a populated
``sync_result``; the job loop then calls ``fetch_result_bytes`` once and
persists the bytes (no polling).

Reconciliation notes (vs. the Sprint-4 template):
  * The ABC ``submit_job(prompt, metadata)`` is 2-arg — the model id / voice /
    text come from ``input_metadata`` (the route enriches it), exactly like
    the Replicate path. No ``model_config`` arg.
  * ``SyncResult`` is URL-based (``result_url`` + ``result_metadata``), not a
    bytes field. The inline bytes are stashed on THIS adapter instance under a
    synthetic ``sync://`` marker; the job loop fetches them back via
    ``fetch_result_bytes`` on the same instance. The key is resolved per
    request and never stored on the instance (no repr/__dict__ leak).
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
from app.services.media_gen.key_resolver import resolve_openai_key
from app.services.media_gen.spend_guard import (
    SpendCapExceeded,
    check_and_reserve_session_budget,
    release_session_budget,
)

_OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
_DEFAULT_MODEL = "gpt-4o-mini-tts"
_DEFAULT_VOICE = "alloy"


class OpenAITTSAdapter(MediaGenAdapter):
    provider_name = "openai"
    # Canonical job_type is "audio_tts" (matches the route + generation_jobs).
    supported_job_types = ["audio_tts"]

    def __init__(
        self,
        key_resolver: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    ) -> None:
        self._resolve_key: Callable[[], Awaitable[Optional[str]]] = (
            key_resolver or resolve_openai_key
        )
        # Per-instance, per-job stash of sync bytes (the job loop fetches them
        # back on the same instance). Never holds a key.
        self._sync_bytes: dict[str, bytes] = {}

    def estimate_cost_usd(self, input_metadata: dict, model_config: Any) -> Decimal:
        char_count = len((input_metadata or {}).get("text", "") or "")
        per_char = Decimal(
            str(getattr(model_config, "cost_per_character", None) or "0.000015")
        )
        return (per_char * char_count).quantize(Decimal("0.0001"))

    async def submit_job(
        self, input_prompt: str, input_metadata: dict
    ) -> SubmitResult:
        meta = input_metadata or {}
        estimate = Decimal(str(meta.get("estimate_usd", "0") or "0"))
        # TTS can legitimately estimate ~$0 for tiny inputs; reserve a
        # non-negative amount but still gate via the session budget.
        if estimate < 0:
            raise SpendCapExceeded("negative cost estimate")
        if not await check_and_reserve_session_budget(estimate):
            raise SpendCapExceeded(f"session budget exceeded (estimate ${estimate})")

        try:
            key = await self._resolve_key()
            if not key:
                raise ProviderKeyMissing("No active OpenAI key for TTS")

            text = meta.get("text") or input_prompt
            voice = meta.get("voice", _DEFAULT_VOICE)
            model = meta.get("tts_model") or _DEFAULT_MODEL
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    _OPENAI_SPEECH_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "input": text,
                        "voice": voice,
                        "response_format": "mp3",
                    },
                )
            if resp.status_code == 401:
                raise ProviderAuthError("OpenAI TTS auth failed")
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
                        "voice": voice,
                        "char_count": len(text),
                    },
                ),
            )
        except Exception:
            await release_session_budget(estimate)
            raise

    async def poll_job(self, external_job_id: str) -> PollResult:
        raise NotImplementedError("OpenAI TTS is synchronous; poll not supported")

    async def fetch_result_bytes(self, result_url: str) -> bytes:
        data = self._sync_bytes.pop(result_url, None)
        if data is None:
            raise ValueError("no synchronous TTS bytes for marker")
        return data
