"""Schemas for the internal media-generation job API (Sprint 2A).

Every credit field carries a sibling ``*_usd`` display string ("$X.XX" or
"<$0.01") — end-user surfaces show USD only; raw credits are debug-only.
IDs are strings (the DB stores UUIDs as String(36)).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field


class VideoGenSubmitRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: str  # model_configs.model identifier
    duration_sec: int = Field(5, ge=1, le=60, strict=True)
    resolution: Literal["auto"] = "auto"  # provider default; no resolution control yet
    has_audio: Literal[False] = False  # no audio control in the current adapter
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class VideoGenSubmitResponse(BaseModel):
    job_id: str
    status: str  # queued or pending_confirm
    estimated_cost_credits: int
    estimated_cost_usd: str
    balance_after_credits: int
    balance_after_usd: str
    requires_confirmation: bool  # estimate_usd >= threshold


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: Optional[int] = None
    output_file_id: Optional[str] = None
    output_url: Optional[str] = None
    actual_cost_credits: Optional[int] = None
    actual_cost_usd: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
