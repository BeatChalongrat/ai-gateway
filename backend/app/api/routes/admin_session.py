"""Admin session auth routes — Phase 2A Commit 3.

Replaces the legacy ``POST /auth/login`` (which returned JWTs in JSON)
with a cookie-based session flow:

* ``POST /auth/session/login``   — email + password → HttpOnly cookie
* ``POST /auth/session/logout``  — clear cookie, revoke session row
* ``GET  /auth/session/me``      — current user info from session

Rate limiting runs before bcrypt so attackers can't spend our CPU.
The miss-path also runs bcrypt (against a stored sentinel hash) to
keep login roughly constant-time regardless of whether the email
exists — a small but real user-enumeration defence.

CSRF: on login we set a non-HttpOnly ``csrf_token`` cookie mirroring
the session's stored CSRF value. A matching ``X-CSRF-Token`` header
will be required on admin mutations by Commit 5.
"""

from __future__ import annotations

import re
import secrets

import bcrypt
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.admin_auth import get_admin_session
from app.api.deps import get_db_session
from app.core.config import settings
from app.core.exceptions import AuthenticationError, RateLimitExceededError
from app.models.admin_user import AdminUserORM
from app.services.admin.login_service import LoginRateLimiter
from app.services.admin.session_manager import SessionManager
from app.services.admin.session_service import (
    get_login_rate_limiter,
    get_session_manager,
)


router = APIRouter(prefix="/auth/session", tags=["admin-auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class LoginRequest(BaseModel):
    # Field is named ``email`` for backward compatibility with the admin-ui
    # JSON contract, but accepts either an email address or a bare username
    # (e.g. ``admin``). The handler resolves against AdminUserORM.email OR
    # AdminUserORM.username.
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _shape_check(cls, v: str) -> str:
        v = (v or "").strip()
        if "@" in v:
            # Email shape — light-touch check, avoids pulling email-validator.
            if "." not in v.split("@")[-1]:
                raise ValueError("invalid identifier")
            return v
        # Username shape — alphanumerics + . _ - only, 1–100 chars.
        if not _USERNAME_RE.match(v):
            raise ValueError("invalid identifier")
        return v

    @field_validator("password")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("password required")
        return v


class UserPayload(BaseModel):
    id: str
    username: str
    email: str
    role: str
    tenant_id: str | None = None


class LoginResponse(BaseModel):
    user: UserPayload


class MeResponse(BaseModel):
    user: UserPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Sentinel bcrypt hash used to keep the miss path running bcrypt.
# Computed once at import time. The plaintext doesn't matter — any
# login attempt against an unknown email runs ``checkpw(candidate,
# _DUMMY_HASH)`` which always returns False but spends the expected
# CPU. Without this, a user-existence oracle is visible via timing.
_DUMMY_HASH = bcrypt.hashpw(b"no-such-user", bcrypt.gensalt(rounds=4)).decode("utf-8")


def _client_ip(request: Request) -> str:
    # Trust the first X-Forwarded-For hop if a reverse proxy is in front;
    # otherwise fall back to the direct peer. In production we expect a
    # proxy that strips untrusted hops before this endpoint sees them.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookies(
    response: Response, *, cookie_value: str, csrf_token: str
) -> None:
    secure = settings.app_env.lower() not in {"development", "test", "testing"}
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.session_idle_ttl_seconds,
        path="/",
    )
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,  # readable by JS for double-submit
        secure=secure,
        samesite="lax",
        max_age=settings.session_idle_ttl_seconds,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie("csrf_token", path="/")


def _user_to_payload(user: AdminUserORM) -> UserPayload:
    return UserPayload(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        tenant_id=getattr(user, "org_id", None),
    )


# ---------------------------------------------------------------------------
# POST /auth/session/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
    session_manager: SessionManager = Depends(get_session_manager),
    rate_limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
):
    # ``identifier`` is either an email or a bare username. The rate-limiter
    # keys on this string, so `admin` and `admin@audit.test` have separate
    # counters — intentional: they're separate login attempts from the
    # limiter's perspective and we don't want to leak which form maps to a
    # real user.
    identifier = body.email.lower()
    ip = _client_ip(request)

    decision = rate_limiter.check(email=identifier, ip=ip)
    if not decision.allowed:
        raise RateLimitExceededError(
            "Too many login attempts. Try again later.",
            code="login_rate_limited",
            details={
                "retry_after_seconds": decision.retry_after_seconds,
                "scope": decision.scope or "unknown",
            },
        )

    user = db.execute(
        select(AdminUserORM).where(
            or_(
                AdminUserORM.email == identifier,
                AdminUserORM.username == identifier,
            )
        )
    ).scalar_one_or_none()

    # Always run bcrypt — either against the real hash or the sentinel.
    # This keeps the miss path ~as slow as the hit path.
    if user is None:
        bcrypt.checkpw(body.password.encode("utf-8"), _DUMMY_HASH.encode("utf-8"))
        rate_limiter.record_failure(email=identifier, ip=ip)
        raise AuthenticationError("Invalid credentials")

    password_ok = bcrypt.checkpw(
        body.password.encode("utf-8"), user.hashed_password.encode("utf-8")
    )
    if not password_ok:
        rate_limiter.record_failure(email=identifier, ip=ip)
        raise AuthenticationError("Invalid credentials")

    if not user.is_active:
        rate_limiter.record_failure(email=identifier, ip=ip)
        raise AuthenticationError("Invalid credentials")

    rate_limiter.record_success(email=identifier, ip=ip)

    # Mint the session. CSRF token is stored in the session payload AND
    # set as a non-HttpOnly cookie — matching header will be enforced on
    # mutating routes by Commit 5.
    csrf_token = secrets.token_urlsafe(32)
    cookie_value, _sid = session_manager.create_session(
        user_id=user.id,
        tenant_id=getattr(user, "org_id", None),
        role=user.role,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    # Attach csrf_token to the session row for double-submit comparison.
    _attach_csrf(session_manager, cookie_value, csrf_token)
    _set_session_cookies(response, cookie_value=cookie_value, csrf_token=csrf_token)
    return LoginResponse(user=_user_to_payload(user))


def _attach_csrf(
    session_manager: SessionManager, cookie_value: str, csrf_token: str
) -> None:
    """Store the csrf_token inside the Redis session row.

    Keeps the secret inside an HttpOnly-only channel (the session
    payload) AND mirrors it on a non-HttpOnly cookie. A CSRF attempt
    must produce a matching header, which can only come from same-
    origin JS that can read the cookie.
    """
    import json

    payload = session_manager.verify_cookie(cookie_value)
    sid = payload["session_id"]
    payload["csrf_token"] = csrf_token
    # Preserve the current TTL — verify_cookie refreshed it already.
    redis = session_manager._redis  # intentionally reach in; narrow API
    key = f"session:{sid}"
    ttl = redis.ttl(key)
    redis.set(key, json.dumps(payload, ensure_ascii=False), ex=max(ttl, 1))


# ---------------------------------------------------------------------------
# POST /auth/session/logout
# ---------------------------------------------------------------------------


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Clear the admin_session cookie + revoke the Redis row.

    Idempotent: calling logout without a cookie, or with a tampered
    one, returns 200 and clears cookies anyway. The client's next
    request will be unauthenticated regardless.
    """
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        try:
            payload = session_manager.verify_cookie(cookie)
            session_manager.revoke_session(payload["session_id"])
        except Exception:
            # Can't verify → nothing to revoke server-side; just drop
            # the cookies below. This is the tampered/expired path.
            pass
    _clear_session_cookies(response)
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /auth/session/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
def me(
    session: dict = Depends(get_admin_session),
    db: Session = Depends(get_db_session),
):
    user = db.get(AdminUserORM, session["user_id"])
    if user is None or not user.is_active:
        raise AuthenticationError("Session user no longer valid")
    return MeResponse(user=_user_to_payload(user))
