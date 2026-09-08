"""Model-level health probe (C2).

A *key* test (``ProviderKeyService.test_key``) only proves the credential
is valid for the provider. It does NOT prove a specific MODEL id is
servable — an aggregator can hold a good key yet 404 on a model that was
renamed or pulled. This module sends the lightest possible real
completion (``max_tokens=1``, a one-word prompt) through the normal
dispatch path and reports whether the model answered.

Reused in two places:
  * the admin "Test" button on a model row (``POST /admin/models/{id}/test``),
  * optionally during the discovery sync, gated behind
    ``settings.model_discovery_health_check`` so the daily sweep doesn't
    fire a paid request per model unless an operator opts in.
"""

from __future__ import annotations

import time

import structlog
from sqlalchemy.orm import Session

from app.core.exceptions import GatewayError
from app.domain.entities import RouteDecision
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest

logger = structlog.get_logger(__name__)

# A one-token probe. ``max_tokens=1`` keeps the spend negligible; the
# prompt is deliberately trivial so no provider rejects it for content.
_PROBE_MESSAGES = [Message(role="user", content="ping")]


async def check_model_health(
    session: Session,
    provider: str,
    model: str,
    *,
    api_key: str | None = None,
) -> dict:
    """Send a 1-token completion to ``(provider, model)``.

    Returns ``{"healthy": bool, "latency_ms": int, "error": str | None}``.
    Resolves a key via :class:`ProviderKeyService` when ``api_key`` is not
    supplied; a missing key is reported as unhealthy (not raised) so the
    caller can render a clean status rather than a 500.
    """
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.services.provider_key_service import ProviderKeyService

    if api_key is None:
        api_key = ProviderKeyService().get_active_key(session, provider, model=model)
    if not api_key:
        return {
            "healthy": False,
            "latency_ms": 0,
            "error": f"No API key configured for provider '{provider}'.",
        }

    payload = ChatCompletionsRequest(
        model=model,
        messages=list(_PROBE_MESSAGES),
        max_tokens=1,
        stream=False,
    )
    route = RouteDecision(provider_name=provider, model_name=model)

    t0 = time.perf_counter()
    try:
        await ProviderDispatcher().dispatch(
            route=route,
            payload=payload,
            messages=list(_PROBE_MESSAGES),
            api_key=api_key,
        )
        ms = round((time.perf_counter() - t0) * 1000)
        return {"healthy": True, "latency_ms": ms, "error": None}
    except GatewayError as exc:
        ms = round((time.perf_counter() - t0) * 1000)
        return {"healthy": False, "latency_ms": ms, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 — never raise out of a probe
        ms = round((time.perf_counter() - t0) * 1000)
        logger.warning(
            "model_health_probe_unexpected_error",
            provider=provider,
            model=model,
            error=str(exc)[:200],
        )
        return {"healthy": False, "latency_ms": ms, "error": str(exc)[:200]}
