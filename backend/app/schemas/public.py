"""Public API schemas — the client-facing contract.

These models define the external API surface.  Provider-specific details
are normalized away before reaching these schemas.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Message, UsageInfo


# ---------------------------------------------------------------------------
# Models / discovery
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    """Single model entry in the /v1/models response (OpenAI-compatible)."""

    id: str
    provider: str


class ModelListResponse(BaseModel):
    """Response for GET /v1/models (OpenAI-compatible ``data`` key)."""

    data: List[ModelInfo]


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------


class ChatCompletionsRequest(BaseModel):
    """Public request for POST /v1/chat/completions.

    Generation parameters (``temperature``, ``max_tokens``, ``top_p``) are
    top-level fields for OpenAI compatibility.  The ``metadata`` dict carries
    gateway-specific extensions (e.g. ``use_case``).
    """

    model: str = "auto"
    messages: List[Message] = Field(..., min_length=1)
    stream: bool = False

    # Generation parameters (optional — provider defaults apply when absent)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Gateway-specific metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Optional per-request policy profile override. Unknown IDs → 422.
    # The project-scope permission check (rejecting an override that
    # would lower restrictiveness) runs later in project_service, AFTER
    # the DB lookup, and raises 403 on a violation.
    policy_profile_id: Optional[str] = None

    @field_validator("policy_profile_id")
    @classmethod
    def _validate_policy_profile_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Import inside the validator so schemas/public.py stays free
        # of a service-layer import at module load time.
        from app.services.policy.profiles import profile_exists
        if not profile_exists(v):
            raise ValueError(f"Unknown policy profile: {v}")
        return v


class ChatCompletionsChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str = "stop"


class GatewayMetadata(BaseModel):
    """Gateway-specific metadata included in the response.

    Keeps provider-agnostic gateway info (policy, routing) separate from
    the OpenAI-compatible core fields.
    """

    policy_action: str = "allow"
    policy_reasons: List[str] = Field(default_factory=list)
    route_reasons: List[str] = Field(default_factory=list)


class ChatCompletionsResponse(BaseModel):
    """Public response for POST /v1/chat/completions.

    Core fields (``id``, ``object``, ``model``, ``choices``, ``usage``,
    ``created``) are OpenAI-compatible.  Gateway extensions live in
    ``provider`` and ``gateway``.
    """

    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    provider: str
    choices: List[ChatCompletionsChoice]
    usage: UsageInfo

    # Gateway extensions
    policy_action: str = "allow"
    policy_reasons: List[str] = Field(default_factory=list)
    cached: bool = False


class FeedbackRequest(BaseModel):
    """Rating for a prior response, linked by request_id."""

    request_id: str
    rating: int = Field(..., description="1 for thumbs-up, -1 for thumbs-down")
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    request_id: str
    rating: int
    recorded_at: datetime
