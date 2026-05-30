"""FastAPI dependencies for request handling.

Supports two auth methods:
- API keys (gw_xxxx): hashed and looked up in api_keys table (for gateway clients)
- JWT tokens (eyJ...): decoded with jwt_secret (for admin UI login)
"""

from __future__ import annotations

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


def get_resolved_api_key(
    authorization: str | None = Header(default=None),
) -> ResolvedApiKey:
    """Validate Bearer token and return resolved identity.

    Accepts both API keys (gw_xxxx) and JWT tokens (eyJ...).
    Raises AuthenticationError (maps to 401) on any failure.

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

    with SessionLocal() as session:
        # JWT tokens start with "eyJ" (base64-encoded JSON header)
        if raw_key.startswith("eyJ"):
            resolved = _resolve_jwt_token(raw_key, session)
            session.commit()
            return resolved

        # Otherwise treat as API key (gw_xxxx)
        api_key: ApiKeyORM | None = _api_key_service.verify_key(session, raw_key)
        if api_key is None:
            raise AuthenticationError("Invalid, expired, or revoked API key")

        resolved = ResolvedApiKey(
            key_id=api_key.key_id,
            tenant_id=api_key.tenant_id,
            project_id=api_key.project_id,
            key_prefix=api_key.key_prefix,
            role=getattr(api_key, "role", "user"),
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
