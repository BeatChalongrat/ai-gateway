"""Portal auth: signup / login / refresh / logout.

These routes do NOT require a portal token. The router is mounted
without the ``get_portal_user`` dependency so unauthenticated clients
can reach them. ``/logout`` accepts a token if provided but the MVP
treats it as a client-side action (drop the token locally) — the
response is constant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.services.portal import auth_service, oauth_service


router = APIRouter()


OAUTH_PROVIDERS = {"google", "github"}


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/signup")
def signup(
    body: SignupRequest,
    session: Session = Depends(get_db_session),
):
    return auth_service.signup(
        session,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
    )


@router.post("/login")
def login(
    body: LoginRequest,
    session: Session = Depends(get_db_session),
):
    return auth_service.login(session, email=body.email, password=body.password)


@router.post("/refresh")
def refresh(
    body: RefreshRequest,
    session: Session = Depends(get_db_session),
):
    return auth_service.refresh(session, refresh_token=body.refresh_token)


@router.post("/logout")
def logout():
    # Portal tokens are stateless JWTs — server-side revocation is not
    # implemented in the MVP. Clients must drop the token on their end.
    return {"message": "ok"}


class OAuthCallbackRequest(BaseModel):
    provider: str = Field(..., pattern="^(google|github)$")
    oauth_id: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=1000)


@router.post("/oauth/callback")
def oauth_callback(
    body: OAuthCallbackRequest,
    session: Session = Depends(get_db_session),
):
    """Exchange a verified OAuth identity for portal tokens.

    Intended to be called by NextAuth's ``signIn`` callback AFTER it has
    verified the provider's ID token / access token. The backend trusts
    the caller — it doesn't re-verify the OAuth assertion — so this
    endpoint should only be reachable from the portal itself, never
    directly by browsers. The portal's NextAuth setup lives on the same
    origin/host.
    """
    return oauth_service.login_or_create(
        session,
        provider=body.provider,
        oauth_id=body.oauth_id,
        email=body.email,
        display_name=body.display_name,
        avatar_url=body.avatar_url,
    )


@router.get("/public-settings")
def public_settings(session: Session = Depends(get_db_session)):
    """Public values safe to read unauthenticated.

    Used by the signup page to render the correct "Get X free tokens"
    label and to hide the form entirely when signups are disabled.
    Intentionally scoped — never expose the full ``system_settings``
    table here.
    """
    from app.services.portal import credit_service

    free_trial_raw = credit_service.get_setting(
        session, "free_trial_tokens", "100000"
    ) or "100000"
    try:
        free_trial = max(0, int(free_trial_raw))
    except ValueError:
        free_trial = 100000

    signup_raw = (
        credit_service.get_setting(session, "portal_signup_enabled", "true") or "true"
    ).strip().lower()
    signup_enabled = signup_raw in ("1", "true", "yes", "on")

    return {
        "free_trial_tokens": free_trial,
        "portal_signup_enabled": signup_enabled,
    }
