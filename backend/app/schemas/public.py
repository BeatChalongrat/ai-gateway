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


class ModelCapabilities(BaseModel):
    """Capability flags on a ``ModelInfo`` for client model-selectors (vision
    picker, the image-gen button, etc.). Built from the ``model_configs``
    booleans; None fields are dropped from the wire by
    ``response_model_exclude_none`` on the route, so a model only advertises
    capabilities it actually has."""

    streaming: Optional[bool] = None
    vision: Optional[bool] = None
    tools: Optional[bool] = None
    reasoning: Optional[bool] = None
    image_generation: Optional[bool] = None
    # Chat models that CAN emit images by calling an image model as a tool.
    # The actual tool wiring is a later phase; this flag just lets the UI
    # light up the image affordance for these models now.
    image_generation_via_tools: Optional[bool] = None
    # DEDICATED video model (direct-dispatch target). The UI shows the video
    # gen form for these (model_kind=video), NOT the chat composer.
    video_generation: Optional[bool] = None
    # Chat models that CAN emit a video by calling the generate_video tool
    # (routes to a dedicated video model). Mirrors image_generation_via_tools;
    # lights up the 🎬 composer button. Kept DISTINCT from video_generation so
    # chat-edge can tell a chat-via-tools model from a dedicated video model.
    video_generation_via_tools: Optional[bool] = None
    # Dedicated media-model capabilities (Sprint 2A.5). Set on the model_configs
    # rows for music / TTS models; surface to the UI for the direct-dispatch
    # forms. (Wire names mirror image_generation/video_generation, NOT
    # supports_*, to keep the capabilities convention consistent.)
    audio_generation: Optional[bool] = None
    tts: Optional[bool] = None


class ModelInfo(BaseModel):
    """Single model entry in the /v1/models response (OpenAI-compatible)."""

    id: str
    provider: str
    # Capability flags (vision/tools/streaming/reasoning/image_generation).
    # Optional + None-excluded so the synthetic ``auto`` entry and models
    # with no flags serialise unchanged.
    capabilities: Optional[ModelCapabilities] = None
    # Reasoning capability metadata (migration 045 / T1). Optional so
    # the synthetic ``id="auto"`` gateway entry — which has no backing
    # ``model_configs`` row — serialises without a default-filled
    # capability block. ``response_model_exclude_none`` on the route
    # drops it from the wire when absent.
    reasoning: Optional[ReasoningCapability] = None
    # Display data from ``model_configs`` (models-catalog step 1) so
    # clients need no static metadata of their own. All optional and
    # None-excluded: emitted only when the DB row carries a real value,
    # so old clients and rows with unknown data see the wire unchanged.
    # Prices are per 1M tokens (the shape both client pickers render).
    context_window: Optional[int] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    # FIX35 Part C — price band (services/auto_router/tiers.py):
    # low | medium | high | xhigh | max. The picker's tier badge and the
    # label of the Auto router's per-conversation ceiling.
    cost_tier: Optional[Literal["low", "medium", "high", "xhigh", "max"]] = None
    # FIX47 Part C — whether the chat picker should offer this model. Set
    # on chat candidates only; the API may call a hidden model as before.
    show_in_chat: Optional[bool] = None
    # FIX65 — the operator's own box (base_urls.SELF_HOSTED_PROVIDERS).
    # Vendor models in the chat picker are curated to a floor (vision +
    # tools + streaming); a self-hosted model is offered as it is, so the
    # picker groups it under "your own" and says what it can do. Sent
    # only when True.
    self_hosted: Optional[bool] = None
    # Who receives what is sent to this model, as a list of recipients can
    # name it (services/providers/recipients.py): ``<route>`` for a direct
    # provider, ``<route>:<company>`` behind a router. Absent for an
    # in-house model (nothing leaves) and for a model whose company cannot
    # be named — which therefore can be on no list.
    recipient: Optional[str] = None
    recipient_company: Optional[str] = None


class ModelListResponse(BaseModel):
    """Response for GET /v1/models (OpenAI-compatible ``data`` key)."""

    data: List[ModelInfo]


# ---------------------------------------------------------------------------
# Image generation (DALL-E)
# ---------------------------------------------------------------------------


class ImageGenerationRequest(BaseModel):
    """Public request for POST /v1/images/generations (OpenAI-compatible)."""

    model: str = "gpt-image-1"
    prompt: str = Field(..., min_length=1, max_length=4000)
    n: int = Field(1, ge=1, le=10)  # dall-e-3 is forced to 1 in the client
    # Union of gpt-image-1 (auto / 1024x1536 / 1536x1024) and DALL-E sizes.
    size: Literal[
        "auto", "256x256", "512x512", "1024x1024",
        "1024x1536", "1536x1024", "1024x1792", "1792x1024",
    ] = "1024x1024"
    # Union of gpt-image-1 (auto/low/medium/high) and dall-e-3 (standard/hd);
    # the client maps it to whatever the chosen model accepts.
    quality: Literal["auto", "low", "medium", "high", "standard", "hd"] = "auto"
    style: Literal["vivid", "natural"] = "vivid"
    response_format: Literal["url", "b64_json"] = "url"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ImageData(BaseModel):
    url: Optional[str] = None
    b64_json: Optional[str] = None
    revised_prompt: Optional[str] = None


class ImageUsage(BaseModel):
    """SRAN extension — per-image cost (image gen is priced per image, not
    per token)."""

    cost_usd: float = 0.0


class ImageGenerationResponse(BaseModel):
    """Public response for POST /v1/images/generations (OpenAI-compatible
    ``created`` + ``data``, plus the SRAN ``usage.cost_usd`` extension)."""

    created: int = Field(default_factory=lambda: int(time.time()))
    data: List[ImageData]
    model: str
    provider: str = "openai"
    usage: ImageUsage


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------


class RoutingHints(BaseModel):
    """Provider-agnostic capability hints sent by the caller alongside
    ``model="auto"`` (T-smart-routing).

    The caller (chat-edge) describes what the turn NEEDS rather than
    naming a model; the gateway's smart router (``smart_router.py``)
    picks the cheapest capable model among providers that currently
    hold a valid API key. Ignored when ``model`` is a concrete id — the
    explicit path is unchanged. All fields optional so a partial hint
    object (or none) still routes.
    """

    needs_vision: bool = False
    needs_reasoning: bool = False
    # The turn ships tool definitions. Routing MUST then land on a model
    # with supports_tools — a tools-blind model silently answers in prose
    # and the tool call never happens.
    has_tools: bool = False
    tool_count: int = 0
    has_code: bool = False
    has_files: bool = False
    complexity: Optional[str] = None      # "low" | "medium" | "high"
    conv_turn_count: int = 0
    language: Optional[str] = None        # "th" | "en" | "zh" | ...
    # FIX35 Part C — cost ceiling the Auto router may spend up to for this
    # turn: "low" | "medium" | "high" | "xhigh" | "max". None = the tenant /
    # deployment default. Only meaningful with model="auto"; an explicit
    # model is never touched by routing (see orchestrator._apply_router_chain).
    tier: Optional[Literal["low", "medium", "high", "xhigh", "max"]] = None
    # FIX102 — what the compiled request needs a model to hold (prompt plus
    # the output reserve chat-edge held back). HARD: a candidate whose
    # catalogue window is smaller would refuse the request, so it is never
    # picked; an empty pool is ``no_capable_model`` with missing=["context"].
    min_context_tokens: int = Field(0, ge=0)
    # FIX102 — the window chat-edge budgeted this conversation against (the
    # model that answered its last turn). SOFT, applied after the tier
    # ceiling: prefer candidates at least this large, so the next turn's
    # budget does not collapse because one turn went to a smaller model.
    preferred_context_tokens: int = Field(0, ge=0)


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

    # Structured-Outputs passthrough (OpenAI ``response_format``, e.g. a
    # json_schema). Forwarded verbatim by the openai_adapter on the
    # standard chat-completions path when present. None (or omitted) is
    # the existing behaviour, so every existing caller stays byte-identical.
    response_format: Optional[dict] = None

    # Reasoning / thinking config — provider-neutral. None (or omitted)
    # is the existing no-reasoning behaviour, so every existing caller
    # stays byte-identical. T1 only accepts + threads this through; T2
    # adapters translate to provider-specific params and surface
    # reasoning deltas back to the client.
    reasoning: Optional[ReasoningConfig] = None

    # Provider-agnostic smart-routing hints. Present ONLY when ``model``
    # == "auto"; the gateway selects a concrete model from key-valid
    # providers using these. None / omitted keeps the existing routing
    # behaviour (semantic → keyword → service chain or explicit pick),
    # so every existing caller stays byte-identical.
    routing_hints: Optional[RoutingHints] = None

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
    # FIX119 — why the model stopped, in the gateway's closed set
    # (docs/contracts/terminal_cause.json → stop_reasons). Additive beside
    # the OpenAI-shaped finish_reason; `unknown` when the provider said
    # nothing, never a guessed clean finish.
    stop_reason: str = "unknown"


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

    # T5 — Anthropic extended-thinking signature. Surfaced so a client
    # running its OWN tool loop can replay the thinking block verbatim (text
    # + this signature) on the continuation turn, which Anthropic requires
    # for interleaved thinking + tool use. Only populated for Anthropic
    # reasoning responses that carry a signed thinking block; dropped from
    # the wire otherwise (response_model_exclude_none).
    reasoning_signature: Optional[str] = None

    # Reasoning downgrade signal (P0). When the caller requested reasoning
    # (``ReasoningConfig.mode != 'off'``) but the request resolved to a
    # non-reasoning model — because no reasoning-capable model was available
    # after routing/policy filtering — we DO NOT silently drop the intent:
    # ``reasoning_downgraded=True`` plus a human-readable
    # ``reasoning_downgrade_reason`` tell the caller the answer was produced
    # without reasoning. Both default to None and are dropped from the wire
    # (``response_model_exclude_none``) on the normal path, so existing
    # callers see no new fields.
    reasoning_downgraded: Optional[bool] = None
    reasoning_downgrade_reason: Optional[str] = None

    # A request the model it named could not answer, answered by another
    # model through the fallback walk: ``requested_model``, ``actual_model``
    # (also ``model`` above), ``reason``, ``attempts`` (the failures, then the
    # model that answered) and ``billing`` (the answering model's price is the
    # one charged) — the fields of the streaming ``fallback_info`` frame.
    # None, and so absent from the wire, on every other response.
    fallback_info: Optional[Dict[str, Any]] = None

    # NOTE (Phase 4A.2.2 / F2): the internal canonical artifact extension
    # is intentionally NOT a field on this public model, so it never appears
    # in the public OpenAPI schema. For managed internal-trusted WebChat
    # requests it is injected as a top-level ``x_sran_dlp`` key on a custom
    # JSONResponse at the route layer (see ``chat_completions``), bypassing
    # public response-model serialization. Public Direct API callers never
    # see it on the wire or in the schema.


class FeedbackRequest(BaseModel):
    """Rating for a prior response, linked by request_id."""

    request_id: str
    rating: int = Field(..., description="1 for thumbs-up, -1 for thumbs-down")
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    request_id: str
    rating: int
    recorded_at: datetime
