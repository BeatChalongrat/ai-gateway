"""Request capability detection for capability-aware fallback.

A silent fallback must never land on a model that can't actually serve
the request. Vision and tools are *hard* requirements: an image-bearing
request routed to a non-vision model would silently ignore the image,
and a tools request routed to a non-tool model would drop the tool call
— both produce a wrong answer that looks like a success.

Reasoning is deliberately NOT treated as a hard requirement. With Auto
Thinking Mode, chat-edge attaches ``reasoning={mode:"auto"}`` to *every*
request, so it's a "think if the model can" hint, not a contract — the
adapter silently ignores it on non-reasoning models. Requiring reasoning
on fallbacks would wrongly exclude almost every candidate, so reasoning
falls back to any capable answerer (it just won't show thinking).

Used by both fallback layers:
  * routing_service._route_auto → filters Layer-1 candidates, and
  * FallbackOrchestrator → filters the Layer-2 cross-provider chain.
"""

from __future__ import annotations

from dataclasses import dataclass

_IMAGE_BLOCK_TYPES = frozenset({"image_url", "image", "input_image"})


@dataclass(frozen=True)
class RequiredCapabilities:
    """The hard capabilities a request needs from any model that serves
    it (including a fallback). Reasoning is intentionally excluded — see
    the module docstring."""

    vision: bool = False
    tools: bool = False

    @property
    def any(self) -> bool:
        return self.vision or self.tools


def request_needs_vision(payload) -> bool:
    """True when any message carries an image content block.

    ``Message.content`` is ``str | list[dict] | None``; only the list
    (multimodal) form can carry images, as ``{"type": "image_url", …}``
    blocks. Defensive against non-dict parts so a malformed block never
    raises here.
    """
    messages = getattr(payload, "messages", None)
    # Only a genuine list is inspected — guards against mock/stub
    # payloads (e.g. MagicMock) whose attribute access is truthy but
    # not iterable, and matches the real schema (messages is a list).
    if not isinstance(messages, (list, tuple)):
        return False
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in _IMAGE_BLOCK_TYPES:
                return True
    return False


def request_needs_tools(payload) -> bool:
    """True when the request declares tools (function calling).

    Requires an actual non-empty list (``tools`` is ``Optional[List]``
    in the schema) so a mock/stub payload doesn't read as tool-using.
    """
    tools = getattr(payload, "tools", None)
    return isinstance(tools, (list, tuple)) and len(tools) > 0


def detect_required_capabilities(payload) -> RequiredCapabilities:
    """Inspect a ChatCompletionsRequest for its hard capability needs."""
    return RequiredCapabilities(
        vision=request_needs_vision(payload),
        tools=request_needs_tools(payload),
    )


def model_meets(required: RequiredCapabilities, model_id: str) -> bool:
    """True if the registry model ``model_id`` satisfies ``required``.

    Used to filter Layer-2 chain candidates (which are bare
    ``{provider, model}`` dicts with no inline capability flags). When
    the model isn't in the registry we can't verify it, so — for a
    request that HAS a hard requirement — we treat it as a miss and skip
    it rather than risk a degraded fallback. With no requirement, any
    model passes.
    """
    if not required.any:
        return True
    from app.services.providers.model_registry import get_candidate

    cand = get_candidate(model_id)
    if cand is None:
        return False
    if required.vision and not getattr(cand, "supports_vision", False):
        return False
    if required.tools and not getattr(cand, "supports_tools", False):
        return False
    return True
