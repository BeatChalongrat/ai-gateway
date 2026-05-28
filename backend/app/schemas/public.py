"""Public API schemas — the client-facing contract.

These models define the external API surface.  Provider-specific details
are normalized away before reaching these schemas.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Message, UsageInfo


# ---------------------------------------------------------------------------
# Reasoning / thinking config — provider-neutral request knob.
# ---------------------------------------------------------------------------


class ReasoningConfig(BaseModel):
    """Unified reasoning / thinking configuration.

    Provider-neutral on the wire. The Gateway translates this into the
    upstream provider's native shape inside each adapter (T2):

      * OpenAI o-series          → ``reasoning_effort``
      * Anthropic Claude 4+      → ``thinking`` block with budget tokens
      * Gemini 2.5+              → ``thinkingConfig.thinkingLevel``
      * DeepSeek-R1 / Groq R1    → adapter parses ``reasoning_content``

    ``mode='off'`` is explicit-disable; sending no ``reasoning`` field
    at all is equivalent (the gateway treats absent + off the same).
    ``show_summary=True`` asks the gateway to surface reasoning text
    back to the caller — when False (the default) the gateway still
    counts reasoning tokens for billing but does not emit reasoning
    deltas on the SSE stream / does not set ``reasoning_content`` on
    the non-streaming response.
    """

    mode: Literal["off", "auto", "low", "medium", "high"] = "off"
    show_summary: bool = False


# ---------------------------------------------------------------------------
# Models / discovery
# ---------------------------------------------------------------------------


class ReasoningCapability(BaseModel):
    """Reasoning / thinking capability block on a ``ModelInfo``.

    Surfaced by GET /v1/models so clients (Chat Edge, SDKs) can
    discover which models accept the public ``ReasoningConfig`` knob
    and what the per-model defaults are. Mirrors the per-row state of
    ``model_configs`` after migration 045.
    """

    supported: bool = False
    modes: List[str] = Field(
        default_factory=lambda: ["off", "auto", "low", "medium", "high"]
    )
    default_mode: str = "auto"
    summary_supported: bool = True


class ModelInfo(BaseModel):
    """Single model entry in the /v1/models response (OpenAI-compatible)."""

    id: str
    provider: str
    # Reasoning capability metadata (migration 045 / T1). Optional so
    # the synthetic ``id="auto"`` gateway entry — which has no backing
    # ``model_configs`` row — serialises without a default-filled
    # capability block. ``response_model_exclude_none`` on the route
    # drops it from the wire when absent.
    reasoning: Optional[ReasoningCapability] = None


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

    # Function-calling passthrough. Client sends the OpenAI tool /
    # tool_choice schemas verbatim; the openai_adapter forwards them
    # into the upstream request, parsers.py surfaces any tool_calls in
    # the response, and the orchestrator threads them through to the
    # client. Typed as plain dict/Any to stay forward-compatible with
    # provider-specific extensions (Anthropic input_schema, JSON-mode
    # constraints, etc.) without gateway-side validation drift.
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Any] = None

    # Reasoning / thinking config — provider-neutral. None (or omitted)
    # is the existing no-reasoning behaviour, so every existing caller
    # stays byte-identical. T1 only accepts + threads this through; T2
    # adapters translate to provider-specific params and surface
    # reasoning deltas back to the client.
    reasoning: Optional[ReasoningConfig] = None

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

    # Reasoning / thinking output. ``reasoning_content`` holds the full
    # thinking summary when the caller requested it via
    # ``ReasoningConfig.show_summary=True`` AND the provider emitted
    # reasoning text. ``reasoning_tokens`` is the count of reasoning-
    # only tokens (billed separately by OpenAI o-series and Anthropic
    # extended thinking) — surfaced on the response so callers can
    # display + audit cost without parsing provider-specific usage
    # nesting. Both default to None; ``response_model_exclude_none``
    # drops them from the wire when unset so existing callers see no
    # new fields.
    reasoning_content: Optional[str] = None
    reasoning_tokens: Optional[int] = None


class FeedbackRequest(BaseModel):
    """Rating for a prior response, linked by request_id."""

    request_id: str
    rating: int = Field(..., description="1 for thumbs-up, -1 for thumbs-down")
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    request_id: str
    rating: int
    recorded_at: datetime
