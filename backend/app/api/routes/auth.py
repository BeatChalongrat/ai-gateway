"""Authentication routes — JWT-based login for admin UI.

POST /auth/login   — authenticate with username/password, return JWT tokens
POST /auth/refresh — exchange refresh token for new access token
GET  /auth/me      — return current user info from JWT
"""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.models.admin_user import AdminUserORM

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MeResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str
    org_id: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"


def _create_access_token(user: AdminUserORM) -> tuple[str, int]:
    """Create an access JWT. Returns (token, expires_in_seconds)."""
    expire_minutes = settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "org_id": user.org_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)
    return token, expire_minutes * 60


def _create_refresh_token(user: AdminUserORM) -> str:
    """Create a refresh JWT (30-day expiry)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=30),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT. Raises AuthenticationError on failure."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid token")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    session: Session = Depends(get_db_session),
):
    """Authenticate with username/email + password, return JWT tokens."""
    user = session.execute(
        select(AdminUserORM).where(
            (AdminUserORM.username == body.username)
            | (AdminUserORM.email == body.username)
        )
    ).scalar_one_or_none()

    if user is None:
        raise AuthenticationError("Invalid credentials")

    password_valid = bcrypt.checkpw(
        body.password.encode("utf-8"),
        user.hashed_password.encode("utf-8"),
    )
    if not password_valid:
        raise AuthenticationError("Invalid credentials")

    if not user.is_active:
        raise AuthenticationError("Account is disabled")

    access_token, expires_in = _create_access_token(user)
    refresh_token = _create_refresh_token(user)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    body: RefreshRequest,
    session: Session = Depends(get_db_session),
):
    """Exchange a valid refresh token for new access + refresh tokens."""
    payload = decode_jwt(body.refresh_token)

    if payload.get("type") != "refresh":
        raise AuthenticationError("Not a refresh token")

    user_id = payload.get("sub")
    user = session.get(AdminUserORM, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")

    access_token, expires_in = _create_access_token(user)
    refresh_token = _create_refresh_token(user)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.get("/me", response_model=MeResponse)
def me(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db_session),
):
    """Return the current admin user's info from their JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_jwt(token)

    if payload.get("type") != "access":
        raise AuthenticationError("Not an access token")

    user_id = payload.get("sub")
    user = session.get(AdminUserORM, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")

    return MeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        org_id=user.org_id,
    )
