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
    tenant_id: str | None,
    tenant_role: str,
    token_version: int,
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
        # Phase 3 tenancy claims. tenant_id may be None for test
        # fixtures that predate Phase 3; production signup always sets
        # it. tenant_role defaults to 'member' — only owners see
        # elevated portal UI.
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        # Rotated by the DB on credential-reset / tenant-change events.
        # The verify path in deps.get_portal_user requires this claim
        # to match the current user row, so stale tokens fail closed.
        "token_version": token_version,
    }
    return jwt.encode(payload, settings.portal_jwt_secret, algorithm=_ALGORITHM)


def create_portal_token(
    user_id: str,
    email: str,
    token_type: Literal["access", "refresh"] = "access",
    *,
    tenant_id: str | None = None,
    tenant_role: str = "member",
    token_version: int = 1,
) -> str:
    """Mint a portal JWT. See module docstring for payload shape.

    Phase 3: pass the caller's tenant_id / tenant_role / token_version
    so the JWT carries enough context to scope every portal request.
    Defaults preserve the pre-Phase-3 behaviour for tests that haven't
    been updated yet (no tenant, 'member' role, version 1).
    """
    return _mint_token(
        sub=user_id,
        email=email,
        tenant_id=tenant_id,
        tenant_role=tenant_role,
        token_version=token_version,
        token_type=token_type,
    )


def _mint_for_user(
    user: PortalUserORM, token_type: Literal["access", "refresh"]
) -> str:
    """Convenience wrapper that pulls every claim from the user row."""
    return create_portal_token(
        user.id,
        user.email,
        token_type,
        tenant_id=user.tenant_id,
        tenant_role=user.tenant_role or "member",
        token_version=user.token_version or 1,
    )


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
        # Phase 3 tenancy. tenant_id may be None transiently for
        # removed members between tenant-drop and re-invite; the UI
        # uses tenant_role to gate owner-only actions.
        "tenant_id": user.tenant_id,
        "tenant_role": user.tenant_role or "member",
        "created_at": user.created_at,
    }


def _issue_tokens(user: PortalUserORM) -> dict:
    return {
        "access_token": _mint_for_user(user, "access"),
        "refresh_token": _mint_for_user(user, "refresh"),
        "user": _user_to_dict(user),
    }


def signup(
    db: Session,
    email: str,
    password: str,
    display_name: str | None = None,
    *,
    invite_code: str | None = None,
    tenant_name: str | None = None,
) -> dict:
    """Register a new portal user + attach to a tenant atomically.

    Phase 3 — P0-4. The signup flow now produces three artefacts
    instead of one:

    1. The portal_user row (as before).
    2. Credit-ledger rows for the free-trial bonus (as before).
    3. Tenant membership. Two paths:
       - ``invite_code`` present → redeem it, join that tenant as
         ``'member'``. ``tenant_name`` is rejected in this branch.
       - ``invite_code`` absent → create a brand-new tenant with the
         user as ``'owner'``. If ``tenant_name`` is not supplied we
         fall back to a default derived from the user's email ("X's
         Team") so the portal UI can skip the name field for the
         simple case.

    Everything happens under a single db flush; the caller's
    transaction wraps this call (same pattern as pre-Phase-3).

    Pre-Phase-3 disabled-signup and duplicate-email gates are
    unchanged.
    """
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValidationError("A valid email is required.", code="invalid_email")
    if not password or len(password) < 8:
        raise ValidationError(
            "Password must be at least 8 characters.",
            code="weak_password",
        )
    if invite_code and tenant_name:
        # One path or the other — never both at once. Stops silent
        # surprises where the user types a tenant name AND pastes a
        # code and we pick one arbitrarily.
        raise ValidationError(
            "Provide an invite code OR a tenant name, not both.",
            code="invite_and_tenant_name_conflict",
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
        # Initial token_version so a freshly-minted JWT matches the DB.
        token_version=1,
    )
    db.add(user)
    db.flush()

    # Tenant assignment — happens inside the same transaction. If this
    # raises, the user row is rolled back too.
    from app.services.portal import tenant_service

    if invite_code:
        tenant_service.redeem_invite(
            db, code=invite_code, redeeming_user_id=user.id
        )
    else:
        effective_name = (
            (tenant_name or "").strip()
            or _default_tenant_name_for_email(email_norm)
        )
        tenant_service.create_tenant_with_owner(
            db, tenant_name=effective_name, owner_user_id=user.id
        )
    db.refresh(user)

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


def _default_tenant_name_for_email(email: str) -> str:
    """Derive a human-ish tenant name when the user doesn't supply one.

    Local-part of the email, first letter capitalised, with "'s Team"
    appended — matches the portal UI's "Welcome, X" nicety without
    forcing users to pick a name upfront.
    """
    local = email.split("@", 1)[0] or "My"
    return f"{local.capitalize()}'s Team"


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
    # Phase 3.5 — real email via Resend (dev-mode → console log).
    # Dispatch is fire-and-forget: a Resend outage logs a warning
    # and does NOT block the user's response. Tests still read the
    # token from the return value in dev/test mode.
    from app.services.email import send_verification_email

    verification_link = (
        f"{settings.portal_public_url.rstrip('/')}/verify-email?token={token}"
    )
    send_verification_email(
        to=user.email,
        display_name=user.display_name,
        verification_link=verification_link,
        language=user.preferred_language or "en",
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
        "access_token": _mint_for_user(user, "access"),
    }
