"""Admin session manager — Phase 2A.

Design contract (see ``AUTH_REDESIGN_DESIGN.md`` §4):

* Cookie value = ``itsdangerous``-signed UUID v4. Browsers never see the
  raw session ID; the HMAC is verified before we ever touch Redis.
* Session payload lives at ``session:<uuid>`` in Redis with a sliding
  idle TTL. An absolute ``created_at`` field enforces a hard cap
  independently of the sliding window.
* A per-user set at ``session:user:<user_id>`` indexes all live
  sessions for a user so ``revoke_all_for_user`` (password reset,
  role change) is O(#sessions) with no scan.
* ``signing_secrets`` is a rotation list. The FIRST element signs
  new cookies; ALL elements are accepted for verification. Rotating
  keys is then: prepend new secret, let old sessions expire, then
  drop the old secret.

All methods are synchronous — the admin request path is already sync
(FastAPI sync deps → SQLAlchemy sync ORM) and redis-py's sync client
is what the rest of the services use.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer


_SALT = "admin-session"


class SessionInvalid(Exception):
    """Cookie is missing, malformed, or fails HMAC verification."""


class SessionExpired(Exception):
    """Cookie signature is valid but the absolute TTL has elapsed."""


class SessionRevoked(Exception):
    """Signature valid but the Redis row is gone (logout / revoke)."""


class SessionManager:
    def __init__(
        self,
        *,
        redis_client,
        signing_secrets: list[str],
        idle_ttl_seconds: int,
        absolute_ttl_seconds: int,
    ) -> None:
        if not signing_secrets:
            raise ValueError("signing_secrets must contain at least one secret")
        if idle_ttl_seconds > absolute_ttl_seconds:
            raise ValueError(
                "idle_ttl_seconds must not exceed absolute_ttl_seconds"
            )

        self._redis = redis_client
        self._secrets = list(signing_secrets)
        self._idle_ttl = idle_ttl_seconds
        self._absolute_ttl = absolute_ttl_seconds

        # The first secret signs. All secrets verify.
        self._signer = URLSafeSerializer(self._secrets[0], salt=_SALT)
        self._verifiers = [
            URLSafeSerializer(s, salt=_SALT) for s in self._secrets
        ]

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        user_id: str,
        tenant_id: str | None,
        role: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, str]:
        """Allocate a new session. Returns (signed_cookie_value, session_id)."""
        session_id = uuid.uuid4().hex
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "ip": ip,
            "user_agent": user_agent,
            "created_at": int(time.time()),
        }

        self._redis.set(
            f"session:{session_id}",
            json.dumps(payload, ensure_ascii=False),
            ex=self._idle_ttl,
        )
        self._redis.sadd(f"session:user:{user_id}", session_id)
        # Keep the user index from leaking forever if all sessions are
        # cleaned up through TTL rather than revoke_all.
        self._redis.expire(
            f"session:user:{user_id}", self._absolute_ttl
        )

        cookie = self._signer.dumps(session_id)
        return cookie, session_id

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------

    def verify_cookie(self, cookie_value: str) -> dict[str, Any]:
        """Return the session payload or raise the right subclass.

        Failure modes are split into three exception types so the
        caller can decide whether to clear the cookie (all three),
        warn the user that their session was ended (Revoked/Expired),
        or silently require re-auth (Invalid).
        """
        if not cookie_value:
            raise SessionInvalid("empty cookie")

        session_id = self._decode_with_any_secret(cookie_value)

        raw = self._redis.get(f"session:{session_id}")
        if raw is None:
            raise SessionRevoked(session_id)

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise SessionInvalid(f"corrupt session row: {exc}") from exc

        now = int(time.time())
        created_at = int(payload.get("created_at", 0))
        if now - created_at >= self._absolute_ttl:
            self._redis.delete(f"session:{session_id}")
            self._redis.srem(
                f"session:user:{payload.get('user_id', '')}", session_id
            )
            raise SessionExpired(session_id)

        # Sliding refresh.
        self._redis.expire(f"session:{session_id}", self._idle_ttl)
        return payload

    def _decode_with_any_secret(self, cookie_value: str) -> str:
        last_exc: Exception | None = None
        for verifier in self._verifiers:
            try:
                return verifier.loads(cookie_value)
            except BadSignature as exc:
                last_exc = exc
        raise SessionInvalid(
            f"cookie signature did not match any known secret: {last_exc}"
        )

    # ------------------------------------------------------------------
    # revoke
    # ------------------------------------------------------------------

    def revoke_session(self, session_id: str) -> None:
        """Delete one session. Idempotent — missing keys are fine."""
        raw = self._redis.get(f"session:{session_id}")
        user_id: str | None = None
        if raw:
            try:
                user_id = json.loads(raw).get("user_id")
            except (ValueError, TypeError):
                user_id = None
        self._redis.delete(f"session:{session_id}")
        if user_id:
            self._redis.srem(f"session:user:{user_id}", session_id)

    def revoke_all_for_user(self, user_id: str) -> int:
        """Kill every live session for a user. Returns count removed."""
        ids = list(self._redis.smembers(f"session:user:{user_id}"))
        if not ids:
            return 0
        keys = [f"session:{sid}" for sid in ids]
        self._redis.delete(*keys)
        self._redis.delete(f"session:user:{user_id}")
        return len(ids)
