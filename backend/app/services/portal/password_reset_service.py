"""Password reset flow (Phase 3.5 — Commit 32).

Two operations, both atomic:

* ``request_reset``: always returns a {"ok": True} shape regardless of
  whether the email exists — no user-existence oracle. If the email
  resolves to an active user, mint a 64-char opaque token, store in
  Redis with a 1-hour TTL, and dispatch the reset email via Resend.
* ``confirm_reset``: verify the token, validate the new password,
  update the bcrypt hash, bump ``token_version`` (invalidates every
  live portal session), and delete the Redis row.

Rate-limiting (3 requests / hour / email) lives in Commit 34 — not
in this service so it can be composed with the existing Phase 2A
``LoginRateLimiter`` pattern instead of baked in here.

Hash algorithm note: Beat's Phase 3.5 spec mentioned "argon2id
(existing Phase 2A pattern)" but the actual Phase 2A hash is bcrypt
(see ``auth_service._hash_password``). Mixing two algorithms would
break existing users on login. Deliberate deviation — we use bcrypt
to match the installed base; migrating the whole codebase to argon2id
is a separate track.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.portal_user import PortalUserORM


_log = logging.getLogger("password_reset")


# Entropy: 48 bytes → 64 chars url-safe. Overkill for an internal tool,
# free to compute.
_TOKEN_BYTES = 48
_REDIS_PREFIX = "password_reset:"
_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour per spec
_BCRYPT_COST = 12  # Match auth_service._hash_password.


@dataclass
class _StoredToken:
    user_id: str
    expires_at: float  # unix timestamp, seconds

    def is_expired(self, now_ts: Optional[float] = None) -> bool:
        import time as _t

        now_ts = now_ts if now_ts is not None else _t.time()
        return now_ts > self.expires_at


def _mint_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _validate_new_password(password: str) -> None:
    """Minimum rules per spec: 8+ chars, at least one letter + one digit."""
    if not password or len(password) < 8:
        raise ValidationError(
            "Password must be at least 8 characters.",
            code="weak_password",
        )
    if len(password) > 200:
        raise ValidationError(
            "Password is too long (maximum 200 characters).",
            code="weak_password",
        )
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationError(
            "Password must contain at least one letter and one number.",
            code="weak_password",
        )


def _redis_key(token: str) -> str:
    return f"{_REDIS_PREFIX}{token}"


def request_reset(
    db: Session,
    redis_client,
    *,
    email: str,
) -> dict:
    """Start a password-reset flow.

    Dispatches a reset email if the email matches an active portal
    user. Returns a constant ``{"ok": True}`` payload either way.
    """
    result = {"ok": True}
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        # Same shape so the caller can't probe by sending garbage
        # emails and watching for a different response.
        return result

    user = db.execute(
        select(PortalUserORM).where(PortalUserORM.email == email_norm)
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        # No user-existence oracle — silent no-op.
        _log.info("password_reset.no_user", extra={"email_norm": email_norm})
        return result

    token = _mint_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=_TOKEN_TTL_SECONDS
    )
    payload = {
        "user_id": user.id,
        "expires_at": expires_at.timestamp(),
    }
    redis_client.set(
        _redis_key(token),
        json.dumps(payload),
        ex=_TOKEN_TTL_SECONDS,
    )

    # Dispatch the email. Send failures do NOT block the caller — the
    # sender logs its own warnings and the user can retry the request.
    from app.services.email import send_password_reset_email

    reset_link = (
        f"{settings.portal_public_url.rstrip('/')}"
        f"/reset-password?token={token}"
    )
    send_password_reset_email(
        to=user.email,
        display_name=user.display_name,
        reset_link=reset_link,
        expires_in_minutes=_TOKEN_TTL_SECONDS // 60,
        language=user.preferred_language or "en",
    )
    _log.info(
        "password_reset.requested",
        extra={"portal_user_id": user.id},
    )

    # Expose the token only in dev/test — tests use it to drive the
    # confirm step without parsing an email.
    if settings.app_env.lower() in ("development", "test", "testing"):
        result["token"] = token
    return result


def confirm_reset(
    db: Session,
    redis_client,
    *,
    token: str,
    new_password: str,
) -> dict:
    """Verify the token + set a new password.

    Raises ``ValidationError(code=invalid_token)`` for a missing /
    expired / already-used token — single shape so an attacker
    cannot tell which failure mode applied.
    """
    if not token or not isinstance(token, str):
        raise ValidationError(
            "Invalid or expired reset token.", code="invalid_token"
        )
    _validate_new_password(new_password)

    key = _redis_key(token)
    raw = redis_client.get(key)
    if raw is None:
        raise ValidationError(
            "Invalid or expired reset token.", code="invalid_token"
        )
    try:
        payload = json.loads(raw)
        stored = _StoredToken(
            user_id=payload["user_id"], expires_at=float(payload["expires_at"])
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # Malformed record — treat as invalid, scrub it so it can't
        # be exploited by whoever wrote the garbage.
        redis_client.delete(key)
        raise ValidationError(
            "Invalid or expired reset token.", code="invalid_token"
        )
    if stored.is_expired():
        redis_client.delete(key)
        raise ValidationError(
            "Invalid or expired reset token.", code="invalid_token"
        )

    # Delete the Redis row BEFORE applying the password change. If the
    # DB update crashes after this, the user has to re-request — which
    # is preferable to the token surviving and being replayable.
    redis_client.delete(key)

    user = db.get(PortalUserORM, stored.user_id)
    if user is None or user.status != "active":
        raise ValidationError(
            "Invalid or expired reset token.", code="invalid_token"
        )

    user.password_hash = bcrypt.hashpw(
        new_password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_COST)
    ).decode("utf-8")
    # Bump so every live portal JWT for this user fails the version
    # check on the next request (C19's token_version gate).
    user.token_version = (user.token_version or 1) + 1
    db.flush()
    _log.info(
        "password_reset.confirmed",
        extra={"portal_user_id": user.id},
    )
    return {"ok": True}
