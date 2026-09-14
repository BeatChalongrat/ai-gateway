"""The provider's own request id (FIX119).

OpenAI answers with ``x-request-id``, Anthropic with ``request-id``; the
OpenAI-compatible boxes (LiteLLM, vLLM, OpenRouter) mostly echo
``x-request-id`` or send nothing. Captured from the response headers at
the moment the stream (or the JSON body) opens, and carried as
``provider_request_id`` on the first chunk / the response, so the
gateway's log line for the turn names the id the provider would recognise.
Never used for anything else: it is a label, not a key.
"""

from __future__ import annotations

import dataclasses

from collections.abc import AsyncIterator, Mapping
from typing import TypeVar

_HEADERS = ("x-request-id", "request-id", "x-amzn-requestid", "cf-ray")

T = TypeVar("T")


def provider_request_id_from(headers: Mapping[str, str] | None) -> str | None:
    """The first recognised request-id header, trimmed to a sane length."""
    if not headers:
        return None
    for name in _HEADERS:
        try:
            value = headers.get(name)
        except Exception:  # noqa: BLE001 — a header map that is not a mapping
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
    return None


def provider_request_id_of(resp: object) -> str | None:
    """``provider_request_id_from`` for a response object — tolerant of a
    test double that has no ``headers`` at all."""
    return provider_request_id_from(getattr(resp, "headers", None))


def with_provider_request_id(obj: T, provider_request_id: str | None) -> T:
    """``obj`` with ``provider_request_id`` set — by ``dataclasses.replace``
    (the provider dataclasses are frozen), and untouched when ``obj`` is not
    a dataclass (a test double) or the id is empty."""
    if not provider_request_id or not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        return obj
    if getattr(obj, "provider_request_id", None) is not None:
        return obj
    try:
        return dataclasses.replace(obj, provider_request_id=provider_request_id)  # type: ignore[type-var]
    except (TypeError, ValueError):
        return obj


async def tag_first_chunk(chunks: AsyncIterator[T], provider_request_id: str | None) -> AsyncIterator[T]:
    """Yield ``chunks`` unchanged, stamping ``provider_request_id`` on the first."""
    first = True
    async for chunk in chunks:
        if first:
            chunk = with_provider_request_id(chunk, provider_request_id)
        first = False
        yield chunk
