"""FIX132 C — the two chat-edge calls behind "delete my account".

* ``request_deletion(user_id)`` → ``POST /internal/accounts/{id}/deletion-request``
  hides every chat of the user at once, stops their scheduled tasks and
  refuses every token issued to them so far on chat-edge.
* ``purge(user_id)`` → ``POST /internal/accounts/{id}/purge`` erases
  everything the user owns in the chat database (chats, file bytes,
  memories, projects, exports, identity rows) when the 30-day window has
  passed.

Same trust anchor as ``chat_edge_revoke``: ``CHAT_EDGE_INTERNAL_URL`` +
``X-Internal-Service-Secret``. Unlike logout's revoke, both calls are
load-bearing — the caller refuses to go on (the deletion request answers
503, the purge waits for the next night) when chat-edge did not confirm.
"""

from __future__ import annotations

import time

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_REQUEST_TIMEOUT_S = 15.0
# A purge erases every conversation of a heavy user one transaction at a
# time; chat-edge finishes it even if we stop waiting (and a repeat call
# resumes), so this only bounds how long the nightly step blocks.
_PURGE_TIMEOUT_S = 600.0
_ATTEMPTS = 3


class ChatEdgeAccountClient:
    """Thin client; tests substitute a fake with the same two methods."""

    def _post(self, path: str, timeout: float) -> dict | None:
        base = (settings.chat_edge_internal_url or "").strip().rstrip("/")
        secret = (settings.internal_service_secret or "").strip()
        if not base or not secret:
            logger.error(
                "chat_edge_account_unconfigured",
                reason="chat_edge_internal_url or internal_service_secret unset",
            )
            return None
        last_err = ""
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    r = client.post(
                        f"{base}{path}",
                        headers={"X-Internal-Service-Secret": secret},
                    )
                if r.status_code == 200:
                    return r.json()
                last_err = f"status {r.status_code}"
                if r.status_code in (400, 401, 404):
                    break  # misconfiguration — retrying will not help
            except Exception as exc:  # noqa: BLE001 — reported below
                last_err = f"{type(exc).__name__}: {str(exc)[:120]}"
            if attempt < _ATTEMPTS:
                time.sleep(0.5 * attempt)
        logger.error("chat_edge_account_call_failed", path=path, error=last_err)
        return None

    def request_deletion(self, user_id: str) -> dict | None:
        return self._post(
            f"/internal/accounts/{user_id}/deletion-request", _REQUEST_TIMEOUT_S
        )

    def purge(self, user_id: str) -> dict | None:
        return self._post(f"/internal/accounts/{user_id}/purge", _PURGE_TIMEOUT_S)
