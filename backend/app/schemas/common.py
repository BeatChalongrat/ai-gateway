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
