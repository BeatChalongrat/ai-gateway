"""ReplicateMusicAdapter — MusicGen via Replicate (Sprint 4).

Reuses ``ReplicateAdapterBase`` wholesale (key resolution, L1 budget, HTTP
submit/poll/cancel/fetch, token isolation). Only the input payload and the
cost estimate differ from video. The model path (e.g. ``meta/musicgen``) is
passed in ``input_metadata['replicate_model']`` by the submit route, same as
video. Registered under provider ``"replicate"`` + job_type ``"music"`` — the
(provider, job_type) registry keeps it distinct from the video adapter.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.media_gen.adapters.replicate_base import ReplicateAdapterBase

# MusicGen tops out at 30s clips; default to a short 8s sample.
_MUSIC_MAX_SEC = 30
_MUSIC_DEFAULT_SEC = 8


def _clamp_music_duration(meta: dict) -> int:
    requested = int((meta or {}).get("duration_sec", _MUSIC_DEFAULT_SEC) or _MUSIC_DEFAULT_SEC)
    return max(1, min(requested, _MUSIC_MAX_SEC))


class ReplicateMusicAdapter(ReplicateAdapterBase):
    provider_name = "replicate"
    supported_job_types = ["music"]

    def _build_input(self, input_prompt: str, meta: dict) -> dict:
        return {"prompt": input_prompt, "duration": _clamp_music_duration(meta)}

    def estimate_cost_usd(self, input_metadata: dict, model_config: Any) -> Decimal:
        duration = _clamp_music_duration(input_metadata)
        per_second = Decimal(
            str(getattr(model_config, "cost_per_second", None) or "0.0002")
        )
        return per_second * duration
