from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    # "tool" is required for function-calling round-trips: the client
    # sends the result of a previously-requested tool_call back to the
    # model as role="tool" with a matching tool_call_id.
    role: Literal["system", "user", "assistant", "tool"]
    # Optional because an assistant message that returns tool_calls
    # may have content=null per the OpenAI spec. Downstream code that
    # reads .content already isinstance-checks for str (DLP scanners,
    # observability snapshots) so a None here doesn't crash.
    #
    # Phase 4 Session 4 — vision: user turns that carry image
    # attachments ship `content` as a list of content blocks
    # ([{"type":"image_url",...}, {"type":"text","text":"..."}])
    # matching OpenAI / Anthropic vision message shape. The DLP
    # scanner already flattens list content to text-only blocks
    # (rust_dlp_client.py); the transformer guards against list
    # content by skipping it (image bytes have no PII).
    content: Optional[Union[str, List[dict]]] = None
    # OpenAI function-calling passthrough. Each entry mirrors the
    # OpenAI tool_call shape:
    #   {"id": str, "type": "function",
    #    "function": {"name": str, "arguments": str}}
    tool_calls: Optional[List[dict]] = None
    # Set on role="tool" messages so the model can match the result
    # back to the call it requested.
    tool_call_id: Optional[str] = None
    # Function/tool name for role="tool" messages.
    name: Optional[str] = None

    # T5 — extended-thinking round-trip (Anthropic). When a client replays
    # a prior assistant turn that used extended thinking BACK to the gateway
    # alongside a tool result, Anthropic requires the original ``thinking``
    # block — including its cryptographic ``signature`` — to be present in
    # the assistant turn next to the ``tool_use`` block, or it rejects the
    # interleaved-thinking continuation. These two optional fields carry it:
    # ``reasoning_content`` is the verbatim thinking text the gateway
    # surfaced on the prior response, ``reasoning_signature`` the opaque
    # signature that came with it. The Anthropic adapter reconstructs the
    # thinking block from them on assistant turns; all other adapters ignore
    # them. Both default None so non-reasoning / non-Anthropic requests are
    # unchanged. NOTE: the signature validates against the EXACT thinking
    # text, so a round-trip only succeeds when the replayed
    # ``reasoning_content`` is the unmasked original (DLP-masked reasoning
    # would fail signature validation — see REASONING_COMPLETE_REPORT.md).
    reasoning_content: Optional[str] = None
    reasoning_signature: Optional[str] = None

    # Suppress None-valued tool fields in serialisation so a plain
    # chat message doesn't gain `"tool_calls": null` noise. The route
    # also sets response_model_exclude_none=True for belt-and-braces.
    model_config = ConfigDict(
        json_schema_extra={"description": "Chat message"},
    )


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
    # Reasoning subdivision of ``completion_tokens`` (T3). Surfaced
    # here so the response wire shape matches OpenAI's
    # ``usage.completion_tokens_details.reasoning_tokens`` convention.
    # Optional so absent-when-not-reasoning is dropped from the wire
    # by ``response_model_exclude_none`` on routes that opt in.
    reasoning_tokens: Optional[int] = None


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
    status: str  # "ok" | "degraded" | "unhealthy"
    service: str
    version: str
    # Component detail: database, db_pool, redis, and per-sidecar
    # reachability (dlp_sidecar / rate_limiter / log_worker). Optional so
    # older clients that only read ``status`` are unaffected.
    checks: dict = {}
