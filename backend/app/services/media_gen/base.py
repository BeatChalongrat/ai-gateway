"""Adapter contract for async media-generation providers (Sprint 1).

Every provider (Replicate, Fal.ai, ElevenLabs, …) is wrapped by a
``MediaGenAdapter`` subclass. The interface deliberately supports BOTH
industry async patterns (audit v2, Part C2):

- ``submit_job`` returns a ``SubmitResult`` carrying the provider's
  external job id. The poll loop then calls ``poll_job`` until terminal.
- A provider that completes SYNCHRONOUSLY (e.g. ElevenLabs TTS returns
  audio inline) sets ``SubmitResult.sync_result`` so the loop skips
  polling entirely.

Webhooks (where a provider supports them) are an optional optimization
layered on later; polling is the mandatory baseline because every
documented async provider exposes a status endpoint.

Sprint 1 ships ONLY this abstract base — no concrete subclass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional


@dataclass
class SyncResult:
    """A result that a synchronous provider returned at submit time."""

    result_url: Optional[str] = None
    result_metadata: Optional[dict] = None


@dataclass
class SubmitResult:
    """Outcome of submitting a job to a provider.

    When ``sync_result`` is not None the provider completed inline and the
    job loop should persist it directly instead of polling.
    """

    external_job_id: str
    initial_status: str
    sync_result: Optional[SyncResult] = None


@dataclass
class PollResult:
    """A single status read for an in-flight job.

    ``status`` is one of ``'processing'`` | ``'done'`` | ``'failed'``.
    """

    status: str
    progress_pct: Optional[int] = None
    result_url: Optional[str] = None
    result_metadata: Optional[dict] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class MediaGenAdapter(ABC):
    """Abstract base for a media-generation provider adapter."""

    #: Stable provider key (matches ``generation_jobs.provider``).
    provider_name: str = ""
    #: Job types this adapter can service (``video``/``audio_tts``/``music``…).
    supported_job_types: list[str] = []

    @abstractmethod
    async def submit_job(
        self, input_prompt: str, input_metadata: dict
    ) -> SubmitResult:
        """Submit a generation job; return its external id (+ optional
        synchronous result)."""
        raise NotImplementedError

    @abstractmethod
    async def poll_job(self, external_job_id: str) -> PollResult:
        """Read current status/result for a previously submitted job."""
        raise NotImplementedError

    @abstractmethod
    def estimate_cost_usd(
        self, input_metadata: dict, model_config: Any
    ) -> Decimal:
        """Estimate the USD cost of a job WITHOUT calling the provider.

        Pure function over the request metadata (duration, character
        count, …) and the model_config cost fields. Used by the Sprint 1
        cost pre-check before a job is accepted.
        """
        raise NotImplementedError

    # --- Optional hooks (default implementations) ---

    async def cancel_job(self, external_job_id: str) -> bool:
        """Best-effort cancel. Default: not supported."""
        return False

    async def fetch_result_bytes(self, result_url: str) -> bytes:
        """Download a finished artifact. Default: httpx GET with retry."""
        import httpx

        last_exc: Exception | None = None
        for _attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(result_url)
                    resp.raise_for_status()
                    return resp.content
            except Exception as exc:  # noqa: BLE001 — retry transient errors
                last_exc = exc
        raise RuntimeError(
            f"failed to fetch result after 3 attempts: {result_url}"
        ) from last_exc
