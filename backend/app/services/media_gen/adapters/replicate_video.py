"""ReplicateVideoAdapter — real video provider (Sprint 2B; Sprint 4 refactor).

The HTTP plumbing, key resolution + token isolation, L1 budget reserve/refund,
polling, cancel, fetch, and API-error metrics now live in
``ReplicateAdapterBase`` (shared with ``ReplicateMusicAdapter``). This module
keeps only what's video-specific: the duration clamp and the cost estimate.

Token isolation, cost-safety, and the "no model_config in submit_job"
metadata-passing contract are unchanged — see ``replicate_base``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.services.media_gen.adapters.replicate_base import (
    ContentPolicyBlocked,
    ProviderAuthError,
    ReplicateAdapterBase,
)

__all__ = [
    "ReplicateVideoAdapter",
    "ProviderAuthError",
    "ContentPolicyBlocked",
]


def _clamp_video_duration(meta: dict) -> int:
    requested = int((meta or {}).get("duration_sec", 5) or 5)
    return max(1, min(requested, settings.replicate_max_video_duration_sec))


class ReplicateVideoAdapter(ReplicateAdapterBase):
    provider_name = "replicate"
    supported_job_types = ["video"]

    def _build_input(self, input_prompt: str, meta: dict) -> dict:
        return {"prompt": input_prompt, "duration": _clamp_video_duration(meta)}

    def estimate_cost_usd(self, input_metadata: dict, model_config: Any) -> Decimal:
        duration = _clamp_video_duration(input_metadata)
        per_second = Decimal(
            str(getattr(model_config, "cost_per_second", None) or "0.07")
        )
        return per_second * duration
