"""Portal user auth: signup, login, JWT mint / verify.

Tokens are signed with ``settings.portal_jwt_secret`` — a SEPARATE key
from the admin JWT — and carry ``type="portal"``. The portal auth
dependency (``app.api.deps.get_portal_user``) rejects tokens without
that marker, so leaking the admin secret can't forge a portal session
and vice versa.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    ValidationError,
)
from app.models.portal_user import PortalUserORM
from app.services.portal import credit_service


_ALGORITHM = "HS256"
_BCRYPT_COST = 12


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_COST)
    ).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _mint_token(
    *,
    sub: str,
    email: str,
    token_type: Literal["access", "refresh"],
) -> str:
    now = datetime.now(timezone.utc)
    if token_type == "access":
        exp = now + timedelta(
            minutes=settings.portal_access_token_expire_minutes
        )
    else:
        exp = now + timedelta(
            days=settings.portal_refresh_token_expire_days
        )
    payload = {
        "sub": sub,
        "email": email,
        "type": "portal",
        "token_use": token_type,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.portal_jwt_secret, algorithm=_ALGORITHM)


def create_portal_token(
    user_id: str,
    email: str,
    token_type: Literal["access", "refresh"] = "access",
) -> str:
    """Mint a portal JWT. See module docstring for payload shape."""
    return _mint_token(sub=user_id, email=email, token_type=token_type)


def verify_portal_token(token: str) -> dict:
    """Decode a portal JWT and return its payload.

    Raises ``AuthenticationError`` if the token is expired, malformed,
    or not a portal token (``type != "portal"``).
    """
    try:
        payload = jwt.decode(
            token, settings.portal_jwt_secret, algorithms=[_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Portal token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid portal token")

    if payload.get("type") != "portal":
        # A token signed with the admin secret won't even verify here
        # (different secret). This guard catches the theoretical case of
        # a portal-signed token with the wrong marker.
        raise AuthenticationError("Not a portal token")

    return payload


def _user_to_dict(user: PortalUserORM) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "plan": user.plan,
        "status": user.status,
        "credit_balance": int(user.credit_balance),
        "credit_used": int(user.credit_used),
        "email_verified": bool(user.email_verified),
        "created_at": user.created_at,
    }


def _issue_tokens(user: PortalUserORM) -> dict:
    return {
        "access_token": create_portal_token(user.id, user.email, "access"),
        "refresh_token": create_portal_token(user.id, user.email, "refresh"),
        "user": _user_to_dict(user),
    }


def signup(
    db: Session,
    email: str,
    password: str,
    display_name: str | None = None,
) -> dict:
    """Register a new portal user.

    Flow:
    1. Check the ``portal_signup_enabled`` system setting — reject with
       ``ValidationError(code=signup_disabled)`` if disabled.
    2. Reject duplicate emails (case-insensitive) with ``ConflictError``.
    3. Hash the password with bcrypt (cost 12).
    4. Read ``free_trial_tokens`` from system settings and open the
       user's balance at that value via ``credit_service.topup_credits``
       (``type=signup_bonus``) — this single source of truth is what the
       "changing the setting affects NEW users only" test relies on.
    5. Return access + refresh tokens along with the user dict.
    """
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValidationError("A valid email is required.", code="invalid_email")
    if not password or len(password) < 8:
        raise ValidationError(
            "Password must be at least 8 characters.",
            code="weak_password",
        )

    signup_enabled = (
        credit_service.get_setting(db, "portal_signup_enabled", "true") or "true"
    ).strip().lower()
    if signup_enabled not in ("1", "true", "yes", "on"):
        raise ValidationError(
            "New signups are temporarily disabled. Please try again later.",
            code="signup_disabled",
        )

    existing = db.execute(
        select(PortalUserORM).where(PortalUserORM.email == email_norm)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "An account with this email already exists.",
            code="email_exists",
        )

    free_trial_raw = credit_service.get_setting(db, "free_trial_tokens", "0") or "0"
    try:
        free_trial = max(0, int(free_trial_raw))
    except ValueError:
        free_trial = 0

    user = PortalUserORM(
        id=str(uuid.uuid4()),
        email=email_norm,
        password_hash=_hash_password(password),
        display_name=(display_name or "").strip() or None,
        credit_balance=0,
        credit_used=0,
        plan="free_trial",
        status="active",
    )
    db.add(user)
    db.flush()

    if free_trial > 0:
        credit_service.topup_credits(
            db,
            user_id=user.id,
            amount=free_trial,
            created_by="system",
            description="Signup bonus",
            type="signup_bonus",
        )
        db.refresh(user)

    return _issue_tokens(user)


def login(db: Session, email: str, password: str) -> dict:
    """Verify credentials and issue portal tokens.

    Phase 2C Commit 14: rejects users whose email is not verified.
    The check runs AFTER the password verify so attackers can't use
    the different response code as a user-existence oracle.
    """
    from app.core.exceptions import AuthorizationError

    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise AuthenticationError("Invalid email or password.")

    user = db.execute(
        select(PortalUserORM).where(PortalUserORM.email == email_norm)
    ).scalar_one_or_none()
    if user is None or not _verify_password(password or "", user.password_hash):
        # Deliberately same message for missing user vs wrong password —
        # don't leak which emails are registered.
        raise AuthenticationError("Invalid email or password.")
    if user.status != "active":
        raise AuthenticationError("Account is not active. Contact support.")

    # Verified-email gate — only enforced for password accounts. OAuth
    # accounts are auto-verified at signup (the provider already did it).
    if not bool(user.email_verified):
        raise AuthorizationError(
            "Please verify your email address before logging in.",
            code="email_not_verified",
            details={"email": user.email},
        )

    return _issue_tokens(user)


# ---------------------------------------------------------------------------
# Email verification (Phase 2C Commit 14)
# ---------------------------------------------------------------------------

_EMAIL_VERIFY_EXPIRY_HOURS = 24


def mint_email_verification_token(user_id: str, email: str) -> str:
    """Create a JWT tagged with ``purpose=email_verify``.

    Short TTL (24h). Signed with the same portal JWT secret so
    rotating that secret automatically invalidates every outstanding
    verification link.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "type": "portal",
        "purpose": "email_verify",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=_EMAIL_VERIFY_EXPIRY_HOURS)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.portal_jwt_secret, algorithm=_ALGORITHM)


def request_verification_email(db: Session, email: str) -> dict:
    """Generate a verification token for the given email.

    Returns a constant 200-shape regardless of whether the email is
    registered — don't leak user existence. In dev/test the token is
    included in the response so callers can proceed without a live
    mailer; in production ``app_env != 'development'`` hides it and
    the token goes out via the email service (stubbed here).
    """
    email_norm = (email or "").strip().lower()
    result: dict = {"ok": True}
    if not email_norm or "@" not in email_norm:
        return result

    user = db.execute(
        select(PortalUserORM).where(PortalUserORM.email == email_norm)
    ).scalar_one_or_none()
    if user is None:
        return result  # silently succeed

    if bool(user.email_verified):
        return result  # already verified — nothing to do

    token = mint_email_verification_token(user.id, user.email)
    # TODO: enqueue email-send in production. For now, log.
    import logging
    logging.getLogger("portal.auth").info(
        "email_verification_token_minted user=%s", user.id
    )
    if settings.app_env.lower() in ("development", "test", "testing"):
        result["token"] = token
    return result


def verify_email(db: Session, token: str) -> dict:
    """Decode the token, assert purpose, flip ``email_verified``."""
    try:
        payload = jwt.decode(
            token, settings.portal_jwt_secret, algorithms=[_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Verification link has expired.")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid verification token.")
    if payload.get("purpose") != "email_verify":
        raise AuthenticationError("Token is not a verification token.")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid verification token.")
    user = db.get(PortalUserORM, user_id)
    if user is None:
        raise AuthenticationError("Account not found.")
    user.email_verified = True
    db.flush()
    return {"ok": True, "email": user.email}


def refresh(db: Session, refresh_token: str) -> dict:
    """Issue a new access token from a valid refresh token."""
    payload = verify_portal_token(refresh_token)
    if payload.get("token_use") != "refresh":
        raise AuthenticationError("Not a refresh token.")
    user = db.get(PortalUserORM, payload["sub"])
    if user is None or user.status != "active":
        raise AuthenticationError("User not found or disabled.")
    return {
        "access_token": create_portal_token(user.id, user.email, "access"),
    }
