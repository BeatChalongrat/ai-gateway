"""FastAPI dependencies for request handling.

Supports two auth methods:
- API keys (gw_xxxx): hashed and looked up in api_keys table (for gateway clients)
- JWT tokens (eyJ...): decoded with jwt_secret (for admin UI login)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generator

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.exceptions import AuthenticationError, AuthorizationError
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
    session = SessionLocal()
    try:
        yield session
        session.commit()
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
    session: Session = Depends(get_db_session),
) -> ResolvedApiKey:
    """Validate Bearer token and return resolved identity.

    Accepts both API keys (gw_xxxx) and JWT tokens (eyJ...).
    Raises AuthenticationError (maps to 401) on any failure.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid authorization header")

    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key:
        raise AuthenticationError("Missing or invalid authorization header")

    # JWT tokens start with "eyJ" (base64-encoded JSON header)
    if raw_key.startswith("eyJ"):
        return _resolve_jwt_token(raw_key, session)

    # Otherwise treat as API key (gw_xxxx)
    api_key: ApiKeyORM | None = _api_key_service.verify_key(session, raw_key)
    if api_key is None:
        raise AuthenticationError("Invalid, expired, or revoked API key")

    return ResolvedApiKey(
        key_id=api_key.key_id,
        tenant_id=api_key.tenant_id,
        project_id=api_key.project_id,
        key_prefix=api_key.key_prefix,
        role=getattr(api_key, "role", "user"),
    )


def require_admin(
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
) -> ResolvedApiKey:
    """Require admin or tenant_admin role for admin endpoints.

    Raises AuthorizationError (403) if role is insufficient.
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
