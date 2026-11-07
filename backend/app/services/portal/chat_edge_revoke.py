"""FIX129 B2 — tell chat-edge that an access token is gone.

The Portal signs the access JWT; chat-edge verifies the signature and,
until FIX129, nothing else — a signed-out token kept every chat route open
for its remaining life (up to 60 min). Logout now revokes the token's
``jti`` in the Portal's Redis (``deps.get_portal_user`` checks it) AND posts
it to chat-edge's ``POST /internal/auth/revoke``, which keeps its own list
in its own Redis and answers 401 from the next request on.

Both sides gate the call with ``INTERNAL_SERVICE_SECRET``. The call is
best-effort with two retries: a chat-edge that cannot be reached is logged
as an error (``chat_edge_revoke_failed``) — the Portal side is still
revoked, and the token still expires — and never turns a logout into a 500.
"""

from __future__ import annotations

import time

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_TIMEOUT_S = 3.0
_ATTEMPTS = 3


def revoke_on_chat_edge(*, jti: str, exp: int, sub: str | None) -> bool:
    """POST the revocation; True when chat-edge acknowledged it."""
    base = (settings.chat_edge_internal_url or "").strip().rstrip("/")
    secret = (settings.internal_service_secret or "").strip()
    if not base or not secret:
        logger.info(
            "chat_edge_revoke_skipped",
            reason="chat_edge_internal_url or internal_service_secret unset",
        )
        return False
    body = {"jti": jti, "exp": int(exp), "sub": sub or ""}
    last_err = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                r = client.post(
                    f"{base}/internal/auth/revoke",
                    json=body,
                    headers={"X-Internal-Service-Secret": secret},
                )
            if r.status_code in (200, 204):
                logger.info("chat_edge_revoked", jti=jti, attempt=attempt)
                return True
            last_err = f"status {r.status_code}"
            if r.status_code in (401, 404):
                break  # misconfiguration — retrying will not help
        except Exception as exc:  # noqa: BLE001 — best effort
            last_err = f"{type(exc).__name__}: {str(exc)[:120]}"
        if attempt < _ATTEMPTS:
            time.sleep(0.2 * attempt)
    logger.error("chat_edge_revoke_failed", jti=jti, error=last_err)
    return False
