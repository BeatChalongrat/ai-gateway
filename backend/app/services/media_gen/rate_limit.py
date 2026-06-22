"""Per-key rate limits for the public media-generation endpoints (Sprint 5).

The chat-edge proxy and external API callers both authenticate with an API
key (``get_resolved_api_key``); these are the cost-control ceilings on top of
that. Reuses the orchestrator's Redis sliding-window limiter (the SAME backend
the image endpoint uses) — never the Rust request-limiter sidecar — and
inherits its fail-open-on-Redis-outage behaviour: an infra blip never blocks a
generation, only real over-use does.

Keyed on ``rate_limit_key_id`` (the trusted per-user key when a shared
web-chat key carries ``X-User-ID``), falling back to ``key_id`` — identical to
the image limiter, so a spoofed ``X-User-ID`` cannot mint fresh quota.
"""

from __future__ import annotations

import time

from app.api.deps import ResolvedApiKey
from app.core.exceptions import RateLimitExceededError
from app.services.orchestrator import GatewayOrchestrator


def _key_for(auth: ResolvedApiKey) -> str:
    return str(getattr(auth, "rate_limit_key_id", None) or auth.key_id)


async def enforce_media_rate_limit(
    auth: ResolvedApiKey,
    *,
    bucket: str,
    limit: int,
    window_seconds: int,
    scope: str,
) -> None:
    """Throttle one media call; raise ``RateLimitExceededError`` (429) on hit.

    ``bucket`` namespaces the Redis window (e.g. ``"video"``); ``scope`` is the
    human label surfaced in the error detail.
    """
    orch = GatewayOrchestrator()
    key = _key_for(auth)
    result = await orch.rate_limit_service.check_window_async(
        f"rl:media:{bucket}:{key}",
        limit,
        window_seconds,
    )
    if not result["allowed"]:
        retry_after = max(1, result["reset_at"] - int(time.time()))
        raise RateLimitExceededError(
            f"Media generation limit reached ({scope}).",
            details={
                "scope": f"media_{bucket}",
                "limit": result["limit"],
                "remaining": result["remaining"],
                "reset_at": result["reset_at"],
            },
            headers={"Retry-After": str(retry_after)},
        )
