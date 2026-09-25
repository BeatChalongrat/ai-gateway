"""OpenAI Moderation endpoint client.

Calls https://api.openai.com/v1/moderations to classify harmful content
(hate, harassment, self-harm, sexual, violence, etc.).

Design constraints:
- Fail-open: any error (timeout, auth failure, network issue, malformed
  response) returns flagged=False with an `error` field set. Moderation
  is a best-effort safety net; infrastructure flakiness must not block
  legitimate requests.
- Input is capped at 4000 characters before sending. OpenAI's endpoint
  accepts longer input but capping keeps latency and cost predictable.
- The OpenAI moderation endpoint is FREE (no token billing), but it
  still requires a valid API key.

FIX134 B4 — the switch and the breaker. The OpenAI provider was turned
off in FIX132 (its key row is_active=false) and every turn still went to
/v1/moderations with whatever fallback key was left, got a 401 and wrote
an INFO ``moderation_scan_complete error=http_401``. Now:

* ``resolve_moderation_key`` (the call sites' single entry) returns no key
  when MODERATION_MODE=off, when the breaker is open, or when neither the
  UI-owned key store nor OPENAI_API_KEY has a usable OpenAI key — and says
  so ONCE (``moderation_disabled`` warning), not per turn.
* A 401/403 from the endpoint opens the breaker for ``BREAKER_SECONDS``
  (30 minutes) for this process with one warning; ``check`` itself makes
  no request while it is open, whoever calls it. After the window one
  probe goes out again (the key may have been fixed).
* A scan that works re-arms the warnings, so the next outage is announced
  again rather than swallowed for the life of the process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

MODERATION_ENDPOINT = "https://api.openai.com/v1/moderations"
MAX_INPUT_CHARS = 4000
DEFAULT_TIMEOUT = 5.0
SCORE_THRESHOLD = 0.1

# FIX134 B4 — how long a rejected key keeps moderation off for this process.
BREAKER_SECONDS = 30 * 60
_BREAKER_STATUSES = frozenset({401, 403})
# Monotonic clock, as a module attribute so tests can move it.
_clock = time.monotonic

_REASON_DETAIL = {
    "moderation_mode_off": "moderation off: MODERATION_MODE=off",
    "no_usable_openai_key": (
        "moderation off: no usable OpenAI key (the openai provider is off, its "
        "keys are inactive/invalid, or none is configured) — profiles with "
        "moderation_enabled run without the /v1/moderations scan until one works"
    ),
}


class _State:
    """Process-wide: the breaker and which reasons were already announced."""

    __slots__ = ("off_until", "off_reason", "announced")

    def __init__(self) -> None:
        self.off_until = 0.0
        self.off_reason: str | None = None
        self.announced: set[str] = set()


_state = _State()


def reset_moderation_state() -> None:
    """Close the breaker and forget the announced reasons (tests)."""
    _state.off_until = 0.0
    _state.off_reason = None
    _state.announced.clear()


def _announce(reason: str, *, detail: str | None = None, **kv) -> None:
    """One ``moderation_disabled`` warning per reason until moderation works again."""
    if reason in _state.announced:
        return
    _state.announced.add(reason)
    logger.warning(
        "moderation_disabled",
        reason=reason,
        detail=detail or _REASON_DETAIL.get(reason, f"moderation off: {reason}"),
        **kv,
    )


def moderation_off_reason(now: float | None = None) -> str | None:
    """Why no moderation call may go out right now, or None."""
    if settings.moderation_mode_effective == "off":
        return "moderation_mode_off"
    if _state.off_reason and (now if now is not None else _clock()) < _state.off_until:
        return _state.off_reason
    return None


def trip_breaker(reason: str) -> None:
    """The endpoint rejected the key: moderation off for BREAKER_SECONDS, one warning."""
    _state.off_until = _clock() + BREAKER_SECONDS
    _state.off_reason = reason
    _state.announced.discard(reason)  # one warning per trip, not one per process
    _announce(
        reason,
        retry_after_s=BREAKER_SECONDS,
        detail=(
            f"moderation off: /v1/moderations rejected the OpenAI key ({reason}); "
            f"no moderation call for {BREAKER_SECONDS // 60} minutes, then one probe"
        ),
    )


async def resolve_moderation_key(session, provider_key_service) -> str | None:
    """The OpenAI key a moderation scan may use on this turn, or None.

    None when MODERATION_MODE=off, while the breaker is open, or when the
    UI-owned key store (``get_api_key_async(session, "openai")`` — inactive,
    invalid and stale keys excluded) and OPENAI_API_KEY both have nothing.
    Each of those is announced once; the per-turn line is DEBUG.
    """
    reason = moderation_off_reason()
    if reason:
        _announce(reason)
        logger.debug("moderation_skipped", reason=reason)
        return None
    key = None
    if session is not None:
        key = await provider_key_service.get_api_key_async(session, "openai")
    key = key or (settings.openai_api_key or "")
    if not key:
        _announce("no_usable_openai_key")
        logger.debug("moderation_skipped", reason="no_usable_openai_key")
        return None
    return key


@dataclass(frozen=True)
class ModerationResult:
    flagged: bool
    categories: dict[str, bool] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    error: str | None = None


class ModerationService:
    """Thin wrapper around OpenAI's /v1/moderations endpoint."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def check(self, text: str, api_key: str) -> ModerationResult:
        reason = moderation_off_reason()
        if reason:
            # FIX134 B4 — off by setting or by the breaker: no request, whoever calls.
            _announce(reason)
            return ModerationResult(flagged=False, error=f"moderation_off:{reason}")
        if not api_key:
            return ModerationResult(flagged=False, error="no_api_key")
        if not text:
            return ModerationResult(flagged=False)

        capped = text[:MAX_INPUT_CHARS]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    MODERATION_ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"input": capped},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            return ModerationResult(flagged=False, error=f"timeout: {exc}"[:200])
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in _BREAKER_STATUSES:
                trip_breaker(f"http_{status}")
            return ModerationResult(flagged=False, error=f"http_{status}")
        except Exception as exc:
            return ModerationResult(flagged=False, error=f"{type(exc).__name__}: {exc}"[:200])

        # The endpoint accepted the key: the next loss of it is news again.
        _state.announced.clear()

        try:
            result = payload["results"][0]
            flagged = bool(result.get("flagged", False))
            categories = {
                k: bool(v) for k, v in result.get("categories", {}).items() if v
            }
            scores = {
                k: round(float(v), 3)
                for k, v in result.get("category_scores", {}).items()
                if float(v) > SCORE_THRESHOLD
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return ModerationResult(
                flagged=False,
                error=f"parse_error: {type(exc).__name__}",
            )

        return ModerationResult(
            flagged=flagged,
            categories=categories,
            scores=scores,
        )
