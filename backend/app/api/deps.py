"""FastAPI dependencies for request handling.

Supports two auth methods:
- API keys (gw_xxxx): hashed and looked up in api_keys table (for gateway clients)
- JWT tokens (eyJ...): decoded with jwt_secret (for admin UI login)
"""

from __future__ import annotations

import dataclasses
import hmac
from dataclasses import dataclass
from typing import Generator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.observability.metrics import DB_POOL_CHECKOUT_TIMEOUTS
from app.models.api_key import ApiKeyORM
from app.services.api_key_service import ApiKeyService


@dataclass(frozen=True)
class ResolvedApiKey:
    """Resolved identity from a validated API key or JWT token."""

    key_id: str
    tenant_id: str
    project_id: str
    key_prefix: str
    role: str = "user"
    # Effective key for the per-request rate limiter. Differs from
    # ``key_id`` only for SHARED API keys carrying a TRUSTED ``X-User-ID``
    # (web-chat → chat-edge → gateway), where it becomes
    # ``<key_id>:user:<user_id>`` so each end-user gets their own bucket.
    # ``None`` => the limiter falls back to ``key_id`` (legacy behaviour).
    rate_limit_key_id: str | None = None
    # TRUSTED end-user portal id behind a shared key (same gate as the
    # rate limiter: ``X-User-ID`` honoured only with the internal-service
    # secret). The credit check/deduction debits THIS user when the key
    # itself isn't portal-owned, so web-chat traffic drains the actual
    # chatting user's balance. ``None`` => key-owner semantics only.
    portal_user_id: str | None = None
    # True only when the caller proved the internal-service secret (the
    # web-chat → chat-edge → gateway path). Used purely as DLP audit
    # metadata (channel=webchat) — NOT a security principal; ownership is
    # still tenant/project/actor. Never trust this to relax policy.
    internal_trusted: bool = False
    # Conversation locator from ``X-Conversation-ID``, honoured ONLY when
    # ``internal_trusted`` (web-chat). For untrusted/public callers this is
    # always ``None`` — a public client's header is ignored, exactly like
    # ``X-User-ID``. This is an opaque locator, NOT proof of ownership.
    trusted_conversation_id: str | None = None
    # Owning portal user of the API key itself (``api_keys.portal_user_id``,
    # already loaded during verification — zero extra queries). Used with
    # ``portal_user_id`` for request-log attribution; the resolution order
    # (owner first, trusted end-user second) mirrors the billing layer's
    # ``portal_credit_check._resolve_billable_user``.
    key_owner_portal_user_id: str | None = None

    @property
    def log_user_id(self) -> str | None:
        """Portal user this request should be attributed to, or None."""
        return self.key_owner_portal_user_id or self.portal_user_id


def get_db_session() -> Generator[Session, None, None]:
    """Yield a transactional DB session, closed after the request."""
    from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except SQLAlchemyTimeoutError:
        # Pool exhausted — a checkout exceeded pool_timeout and 500'd.
        # Audit H4: count it so the connection-per-request ceiling is
        # observable instead of only surfacing as opaque 500s.
        DB_POOL_CHECKOUT_TIMEOUTS.inc()
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_api_key_service = ApiKeyService()


def _resolve_jwt_token(token: str, session: Session) -> ResolvedApiKey:
    """Decode a JWT access token and return a ResolvedApiKey.

    JWT auth is used by the admin UI after POST /auth/login.
    """
    from app.api.routes.auth import decode_jwt
    from app.models.admin_user import AdminUserORM

    payload = decode_jwt(token)

    if payload.get("type") != "access":
        raise AuthenticationError("Not an access token")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    user = session.get(AdminUserORM, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")

    # Map admin user role to RBAC role name
    role = user.role
    if role == "super_admin":
        role = "admin"
    elif role in ("org_admin", "tenant_admin"):
        role = "tenant_admin"
    elif role not in ("admin", "tenant_admin", "project_admin", "user", "viewer"):
        role = "admin"

    return ResolvedApiKey(
        key_id=f"jwt:{user.id}",
        tenant_id=user.org_id or "__global__",
        project_id="__admin__",
        key_prefix=f"jwt:{user.username[:8]}",
        role=role,
    )


def _internal_signal_trusted(secret: str | None) -> bool:
    """True only when the caller proves it's a trusted internal service.

    Compares the request's ``X-Internal-Service-Secret`` against the
    env-configured ``INTERNAL_SERVICE_SECRET`` in constant time. Returns
    False (the safe default) whenever the secret is unset on the server
    OR absent/mismatched on the request — so an external, untrusted
    client can never clear this gate.
    """
    from app.core.config import settings

    configured = (settings.internal_service_secret or "").strip()
    if not configured:
        return False
    provided = (secret or "").strip()
    if not provided:
        return False
    return hmac.compare_digest(provided, configured)


def resolve_rate_limit_key_id(
    *,
    is_api_key: bool,
    base_key_id: str,
    x_user_id: str | None,
    x_internal_service_secret: str | None,
) -> str:
    """Compute the effective rate-limit bucket key.

    Behaviour (matches the auth matrix in the rate-limit redesign):

    * JWT / admin-session identity (``is_api_key=False``): ``base_key_id``
      is already per-user (``jwt:<id>`` / ``session:<id>``) — returned
      unchanged.
    * API key with NO trusted ``X-User-ID``: ``base_key_id`` (the shared
      ``api_key.key_id``) — unchanged legacy behaviour. This is the path
      both normal external clients AND external clients SPOOFING
      ``X-User-ID`` (without the internal secret) take.
    * API key WITH a trusted ``X-User-ID``: ``<base_key_id>:user:<uid>``
      so each end-user behind a shared key gets an independent counter.
    """
    if not is_api_key:
        return base_key_id
    if not _internal_signal_trusted(x_internal_service_secret):
        return base_key_id
    user = (x_user_id or "").strip()
    if not user:
        return base_key_id
    return f"{base_key_id}:user:{user}"


def get_resolved_api_key(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_internal_service_secret: str | None = Header(default=None),
    x_conversation_id: str | None = Header(default=None),
) -> ResolvedApiKey:
    """Validate Bearer token and return resolved identity.

    Accepts both API keys (gw_xxxx) and JWT tokens (eyJ...).
    Raises AuthenticationError (maps to 401) on any failure.

    ``X-User-ID`` + ``X-Internal-Service-Secret`` are read here to derive
    the per-request rate-limit bucket (``ResolvedApiKey.rate_limit_key_id``)
    without trusting an arbitrary external client — see
    ``resolve_rate_limit_key_id``.

    Track A: verification runs on a SHORT-LIVED sync session that closes
    (releasing the pooled connection) the moment auth finishes — it is NOT
    a ``Depends(get_db_session)`` session held for the whole request. The
    pre-flip dependency held a sync connection through the ~1-3s provider
    call, so peak concurrency was capped at the sync pool size (the A.6
    benchmark caught this: a small sync pool exhausted under a concurrent
    burst). A short-lived session keeps the sync pool tiny and removes the
    auth connection-hold ceiling, mirroring the chat session's release.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid authorization header")

    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key:
        raise AuthenticationError("Missing or invalid authorization header")

    # Internal-service trust gate (same as the rate-limiter / portal-user
    # gate). ``X-Conversation-ID`` is an opaque locator honoured ONLY for
    # a trusted internal caller (web-chat → chat-edge); a public client's
    # header is ignored.
    _internal_trusted = _internal_signal_trusted(x_internal_service_secret)
    _trusted_conv_id = (
        ((x_conversation_id or "").strip() or None)
        if _internal_trusted
        else None
    )

    with SessionLocal() as session:
        # JWT tokens start with "eyJ" (base64-encoded JSON header)
        if raw_key.startswith("eyJ"):
            resolved = _resolve_jwt_token(raw_key, session)
            session.commit()
            # JWT identity is already per-user; pin the rate-limit bucket
            # to its key_id explicitly (X-User-ID is ignored on this path).
            return dataclasses.replace(
                resolved,
                rate_limit_key_id=resolved.key_id,
                internal_trusted=_internal_trusted,
                trusted_conversation_id=_trusted_conv_id,
            )

        # Otherwise treat as API key (gw_xxxx)
        api_key: ApiKeyORM | None = _api_key_service.verify_key(session, raw_key)
        if api_key is None:
            raise AuthenticationError("Invalid, expired, or revoked API key")

        rate_limit_key_id = resolve_rate_limit_key_id(
            is_api_key=True,
            base_key_id=api_key.key_id,
            x_user_id=x_user_id,
            x_internal_service_secret=x_internal_service_secret,
        )
        # Same trust gate as the limiter: only a caller proving the
        # internal-service secret gets its X-User-ID honoured as the
        # credit-debit identity.
        trusted_user = (x_user_id or "").strip()
        portal_user_id = (
            trusted_user
            if trusted_user
            and _internal_signal_trusted(x_internal_service_secret)
            else None
        )
        resolved = ResolvedApiKey(
            key_id=api_key.key_id,
            tenant_id=api_key.tenant_id,
            project_id=api_key.project_id,
            key_prefix=api_key.key_prefix,
            role=getattr(api_key, "role", "user"),
            rate_limit_key_id=rate_limit_key_id,
            portal_user_id=portal_user_id,
            internal_trusted=_internal_trusted,
            trusted_conversation_id=_trusted_conv_id,
            key_owner_portal_user_id=getattr(api_key, "portal_user_id", None),
        )
        session.commit()
        return resolved


def require_admin(
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
) -> ResolvedApiKey:
    """Require admin or tenant_admin role for admin endpoints.

    Raises AuthorizationError (403) if role is insufficient.

    NOTE (Phase 2B Commit 9): this function still accepts API-key +
    Bearer-JWT auth via ``get_resolved_api_key``. Admin routers now
    use ``require_admin_session`` instead, which closes the Bearer
    path. ``require_admin`` is preserved for callers that are NOT
    on the admin router surface.
    """
    from app.domain.rbac import Role, has_role_level

    try:
        role = Role[auth.role.upper()]
    except (KeyError, AttributeError):
        role = Role.USER

    if not has_role_level(role, Role.PROJECT_ADMIN):
        raise AuthorizationError(
            "Insufficient permissions for admin operations.",
            details={"role": auth.role, "required": "project_admin"},
        )
    return auth


def require_admin_session(
    request: Request,
    session_manager=Depends(lambda: None),
) -> ResolvedApiKey:
    """Session-cookie-only admin auth (Phase 2B Commit 9).

    Closes the remaining hole from P0-1: the router-level
    ``Depends(require_admin)`` on admin endpoints used to accept any of
    (a) gw_xxx API keys, (b) admin JWT bearer tokens. The cookie-
    session flow is now the only way to reach the admin surface.

    Behaviour:
      * If an ``Authorization: Bearer`` header is present → 401.
        We block Bearer explicitly so misconfigured clients see a
        clear error rather than silently falling through.
      * Reads the ``admin_session`` cookie via ``get_admin_session``
        (HMAC-verified + Redis-backed + TTL-checked).
      * Enforces caller role >= PROJECT_ADMIN (matches the old
        ``require_admin`` contract).
      * Returns a ``ResolvedApiKey`` shaped object so existing admin
        route bodies that take ``auth: ResolvedApiKey`` keep
        working without a per-route rewrite.
    """
    from app.domain.rbac import Role, has_role_level

    auth_hdr = (request.headers.get("authorization") or "").strip()
    if auth_hdr.lower().startswith("bearer "):
        raise AuthenticationError(
            "Bearer tokens are not accepted on admin endpoints. "
            "Log in via POST /auth/session/login and resend with the "
            "session cookie."
        )

    # Manually resolve the session — we can't Depends(get_admin_session)
    # cleanly from a function that FastAPI itself treats as a dep target,
    # so we call the underlying manager directly. Mirrors the logic in
    # get_admin_session but without the Depends-injected manager kwarg.
    from app.core.config import settings
    from app.services.admin.session_manager import (
        SessionExpired,
        SessionInvalid,
        SessionRevoked,
    )

    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        raise AuthenticationError("No admin session cookie")
    try:
        session = session_manager.verify_cookie(cookie)
    except (SessionInvalid, SessionExpired, SessionRevoked) as exc:
        raise AuthenticationError(str(exc)) from exc

    role_str = (session.get("role") or "user").upper()
    try:
        role = Role[role_str]
    except KeyError:
        role = Role.USER
    if not has_role_level(role, Role.PROJECT_ADMIN):
        raise AuthorizationError(
            "Insufficient permissions for admin operations.",
            details={"role": role_str.lower(), "required": "project_admin"},
        )

    return ResolvedApiKey(
        key_id=f"session:{session.get('user_id', '?')}",
        tenant_id=session.get("tenant_id") or "__global__",
        project_id="__admin__",
        key_prefix=f"sess:{(session.get('session_id') or '')[:8]}",
        role=role_str.lower(),
    )


def _install_admin_session_manager_dep() -> None:
    """Late-binding — rewrite the ``session_manager`` default on
    ``require_admin_session`` to ``Depends(get_session_manager)``.

    Done post-definition to avoid a circular import
    (deps → session_service → session_manager → settings). Tests override
    ``get_session_manager`` via ``app.dependency_overrides`` and the
    new default picks that up.
    """
    import inspect as _inspect

    from app.services.admin.session_service import get_session_manager

    sig = _inspect.signature(require_admin_session)
    params = []
    for name, p in sig.parameters.items():
        if name == "session_manager":
            p = p.replace(default=Depends(get_session_manager))
        params.append(p)
    require_admin_session.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]


_install_admin_session_manager_dep()


def check_tenant_access(auth: ResolvedApiKey, target_tenant_id: str) -> None:
    """Verify the caller can access resources for the target tenant.

    Global ADMINs can access any tenant. Lower roles can only access
    their own tenant.

    Raises AuthorizationError (403) on cross-tenant access.
    """
    from app.domain.rbac import Role

    try:
        role = Role[auth.role.upper()]
    except (KeyError, AttributeError):
        role = Role.USER

    if role >= Role.ADMIN:
        return  # Global admin — unrestricted

    if auth.tenant_id != target_tenant_id:
        raise AuthorizationError(
            "Access denied: resource belongs to a different tenant.",
            details={"caller_tenant": auth.tenant_id, "target_tenant": target_tenant_id},
        )


def get_portal_user(
    request: Request = None,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db_session),
):
    """Resolve the caller to a PortalUserORM from a portal JWT.

    Token source priority:
      1. The ``portal_access_token`` HttpOnly cookie (browsers — set by
         the backend on login/refresh).
      2. ``Authorization: Bearer <jwt>`` header (non-browser API clients
         / mobile / backward compat).

    Only accepts tokens minted by ``auth_service.create_portal_token``
    (signed with ``portal_jwt_secret`` and carrying ``type="portal"``).
    Admin JWTs are signed with a different secret and will fail here.

    Raises:
        AuthenticationError (401): missing/invalid/expired token, or
            the token's subject user no longer exists.
        AuthorizationError (403): user exists but is suspended/deleted.
    """
    from app.models.portal_user import PortalUserORM
    from app.services.portal.auth_service import verify_portal_token
    from app.services.portal.cookies import ACCESS_COOKIE

    # request is None only when called directly (unit tests); FastAPI
    # always injects it for HTTP requests.
    token = request.cookies.get(ACCESS_COOKIE) if request is not None else None
    if not token:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("Missing or invalid authorization header")
        token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationError("Missing or invalid authorization header")

    payload = verify_portal_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    user = session.get(PortalUserORM, user_id)
    if user is None:
        raise AuthenticationError("User not found")
    if user.status != "active":
        raise AuthorizationError(
            "Account is not active.",
            code="account_suspended",
            details={"status": user.status},
        )

    # Phase 3 Concern 1: reject stale JWTs whose token_version no
    # longer matches the user row. The DB value gets bumped on
    # migration 029, credential reset, and any explicit session
    # revocation event. Tokens minted before any such bump become
    # invalid on the next request.
    token_version = payload.get("token_version")
    expected_version = user.token_version or 1
    # Tokens minted before Phase 3 have no claim at all; treat absent
    # as version 0 so the compare always fails after the migration
    # bumped every user to 2 — that's the one-time re-login event.
    observed_version = token_version if token_version is not None else 0
    if observed_version != expected_version:
        raise AuthenticationError(
            "Session is no longer valid. Please log in again.",
            code="token_version_stale",
        )

    return user
