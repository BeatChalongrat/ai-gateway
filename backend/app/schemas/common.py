from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # Display-layer additions populated by ``currency_service.format_cost``.
    # ``estimated_cost_usd`` stays the canonical numeric — these two are
    # *additive*, defaulted to None so existing serialisations and
    # response contracts that don't set them continue to behave.
    cost_thb: Optional[float] = None
    cost_display: Optional[str] = None


class ErrorDetail(BaseModel):
    """Machine-readable error body."""

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Unified error envelope returned by all error responses."""

    error: ErrorDetail
    request_id: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
