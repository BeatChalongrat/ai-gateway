"""Schemas for the public direct-dispatch media-generation API (Sprint 2A.5).

Mirror the internal media schemas but for public callers (API key /
session, not HMAC). Every credit field carries a sibling ``*_usd``
display string; the submit response includes a ``poll_url``. IDs are
strings (the DB stores UUIDs as String(36)).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VideoGenPublicRequest(BaseModel):
    prompt: str
    model: str
    duration_sec: int = 5
    resolution: str = "1080p"
    has_audio: bool = False
    conversation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class VideoGenPublicResponse(BaseModel):
    job_id: str
    status: str  # "queued"
    estimated_cost_credits: int
    estimated_cost_usd: str
    balance_after_credits: int
    balance_after_usd: str
    requires_confirmation: bool
    poll_url: str


class AudioGenPublicRequest(BaseModel):
    prompt: str  # TTS: text to speak; music: the prompt
    model: str
    mode: str  # "tts" | "music"
    voice_id: Optional[str] = None  # TTS only
    duration_sec: Optional[int] = None  # music only
    conversation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class AudioGenPublicResponse(BaseModel):
    job_id: str
    status: str
    estimated_cost_credits: int
    estimated_cost_usd: str
    balance_after_credits: int
    balance_after_usd: str
    requires_confirmation: bool
    poll_url: str


class JobStatusPublicResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: Optional[int] = None
    output_url: Optional[str] = None
    # Provider-disclosed provenance (Sprint 5) — C2PA / watermark flags,
    # seed, model id. Surfaced from output_metadata.provenance; the internal
    # output_path is deliberately NOT exposed.
    provenance: Optional[dict] = None
    actual_cost_credits: Optional[int] = None
    actual_cost_usd: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
