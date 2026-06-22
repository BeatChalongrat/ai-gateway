"""Schemas for the internal media-generation job API (Sprint 2A).

Every credit field carries a sibling ``*_usd`` display string ("$X.XX" or
"<$0.01") — end-user surfaces show USD only; raw credits are debug-only.
IDs are strings (the DB stores UUIDs as String(36)).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VideoGenSubmitRequest(BaseModel):
    prompt: str
    model: str  # model_configs.model identifier
    duration_sec: int = 5
    resolution: str = "1080p"  # reserved for future use
    has_audio: bool = False  # reserved for future use
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class VideoGenSubmitResponse(BaseModel):
    job_id: str
    status: str  # "queued" at submit
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
