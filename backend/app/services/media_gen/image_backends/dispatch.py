"""Image fallback walker (FIX67 Part 2/3).

Walks a list of usable destinations (already filtered + capped to ≤2 tiers by
``destinations.usable_destinations``), trying each backend in order. The first
that produces wins; its provider/model rides back on the result for attribution.
A backend that raises is logged and the next tier is tried. If every tier
fails, the LAST error is re-raised so the route maps a real provider failure
(e.g. the owner's out-of-credits gpt-image-1) rather than a generic one.

This is the seam the ``mock`` backend proves live: primary ``openai/gpt-image-1``
raises (no credit) → second tier ``mock`` produces → result.provider == "mock".
"""

from __future__ import annotations

from typing import Callable, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProviderUnavailableError
from app.services.media_gen.destinations import Destination
from app.services.media_gen.image_backends.base import (
    ImageGenResult,
    get_image_backend,
)

logger = structlog.get_logger(__name__)


async def generate_with_fallback(
    session: AsyncSession,
    destinations: list[Destination],
    *,
    prompt: str,
    n: int = 1,
    size: str = "1024x1024",
    quality: str = "auto",
    style: str = "vivid",
    response_format: str = "b64_json",
    request_id: str | None = None,
    on_attempt: Optional[Callable[[Destination, bool, Optional[Exception]], None]] = None,
) -> ImageGenResult:
    """Produce an image from the first working destination, or raise.

    ``on_attempt(destination, ok, error)`` is called after each attempt (for
    tests / metrics). Never mutates its inputs.
    """
    last_exc: Exception | None = None
    for tier, dest in enumerate(destinations):
        backend = get_image_backend(dest.provider)
        if backend is None:
            # A destination whose provider has no registered backend: skip, do
            # not count as a real failure.
            logger.warning(
                "image_destination_no_backend",
                provider=dest.provider, model=dest.model, tier=tier, request_id=request_id,
            )
            continue
        try:
            result = await backend.generate(
                session,
                model=dest.model,
                prompt=prompt,
                n=n,
                size=size,
                quality=quality,
                style=style,
                response_format=response_format,
                request_id=request_id,
            )
            logger.info(
                "image_destination_produced",
                provider=dest.provider, model=dest.model, tier=tier,
                fell_back=tier > 0, request_id=request_id,
            )
            if on_attempt is not None:
                on_attempt(dest, True, None)
            return result
        except Exception as exc:  # noqa: BLE001 — try the next tier, remember why
            last_exc = exc
            logger.warning(
                "image_destination_failed",
                provider=dest.provider, model=dest.model, tier=tier,
                error=str(exc)[:200], error_code=getattr(exc, "code", type(exc).__name__),
                request_id=request_id,
            )
            if on_attempt is not None:
                on_attempt(dest, False, exc)
            continue
    if last_exc is not None:
        raise last_exc
    raise ProviderUnavailableError(
        "No usable image destination is configured.",
        details={"reason": "empty_image_order"},
    )
