"""Mock video adapter (Sprint 2A) — zero external calls, zero cost.

Returns a bundled royalty-free H.264 sample MP4 on a simulated timeline
so the whole video vertical can be exercised end-to-end before the real
Replicate adapter lands in Sprint 2B. The progress timeline is wall-clock
based and tunable via env so tests can run fast:

    MOCK_VIDEO_HALF_SECONDS  (default 5)  → progress 10 → 50
    MOCK_VIDEO_DONE_SECONDS  (default 10) → progress 50 → done
"""

from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.services.media_gen.base import (
    MediaGenAdapter,
    PollResult,
    SubmitResult,
)

_SAMPLE = Path(__file__).resolve().parent / "sample.mp4"

# In-process timeline: external_job_id → monotonic start time.
_STARTS: dict[str, float] = {}


def _half_seconds() -> float:
    try:
        return float(os.getenv("MOCK_VIDEO_HALF_SECONDS", "5"))
    except (TypeError, ValueError):
        return 5.0


def _done_seconds() -> float:
    try:
        return float(os.getenv("MOCK_VIDEO_DONE_SECONDS", "10"))
    except (TypeError, ValueError):
        return 10.0


class MockVideoAdapter(MediaGenAdapter):
    provider_name = "mock"
    supported_job_types = ["video"]

    async def submit_job(
        self, input_prompt: str, input_metadata: dict
    ) -> SubmitResult:
        external_id = f"mock_{uuid.uuid4().hex[:12]}"
        _STARTS[external_id] = time.monotonic()
        return SubmitResult(
            external_job_id=external_id,
            initial_status="processing",
            sync_result=None,
        )

    async def poll_job(self, external_job_id: str) -> PollResult:
        started = _STARTS.get(external_job_id)
        if started is None:
            # Unknown id (e.g. a process restart mid-job) — restart the clock.
            started = time.monotonic()
            _STARTS[external_job_id] = started
        elapsed = time.monotonic() - started
        if elapsed >= _done_seconds():
            return PollResult(
                status="done",
                progress_pct=100,
                result_url=str(_SAMPLE),
                result_metadata={"content_type": "video/mp4"},
            )
        if elapsed >= _half_seconds():
            return PollResult(status="processing", progress_pct=50)
        return PollResult(status="processing", progress_pct=10)

    def estimate_cost_usd(
        self, input_metadata: dict, model_config: Any
    ) -> Decimal:
        duration = int((input_metadata or {}).get("duration_sec", 5) or 5)
        rate = getattr(model_config, "cost_per_second", None)
        if rate is None:
            raise ValueError(
                "model_config.cost_per_second is required for video"
            )
        return Decimal(str(rate)) * duration

    async def fetch_result_bytes(self, result_url: str) -> bytes:
        # For the mock the result is always the bundled sample.
        return _SAMPLE.read_bytes()
