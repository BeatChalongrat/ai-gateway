"""Portal auth: signup / login / refresh / logout.

These routes do NOT require a portal token. The router is mounted
without the ``get_portal_user`` dependency so unauthenticated clients
can reach them. ``/logout`` accepts a token if provided but the MVP
treats it as a client-side action (drop the token locally) — the
response is constant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.exceptions import ValidationError
from app.services.portal import auth_service, oauth_flow_service, oauth_service
from app.services.portal.oauth_flow_service import (
    IdTokenVerificationFailed,
    InvalidReturnUrl,
    OAuthStateStore,
    generate_pkce,
    get_oauth_state_store,
    validate_return_to,
)


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


# ---------------------------------------------------------------------------
# Phase 2C Commit 14 — email verification flow
# ---------------------------------------------------------------------------


class VerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=4096)


@router.post("/request-verification-email")
def request_verification_email(
    body: VerificationRequest,
    session: Session = Depends(get_db_session),
):
    """Start (or restart) the email-verification flow for an address.

    Returns 200 + ``{"ok": True}`` regardless of whether the email is
    registered or already verified — prevents user-existence
    probing. In dev/test the response also contains the minted token
    so integration tests can drive the flow without a mail server.
    """
    return auth_service.request_verification_email(session, email=body.email)


@router.post("/verify-email")
def verify_email(
    body: VerifyEmailRequest,
    session: Session = Depends(get_db_session),
):
    """Consume a verification token and flip the ``email_verified`` flag."""
    return auth_service.verify_email(session, token=body.token)


@router.post("/logout")
def logout():
    # Portal tokens are stateless JWTs — server-side revocation is not
    # implemented in the MVP. Clients must drop the token on their end.
    return {"message": "ok"}


@router.post("/oauth/callback", status_code=410)
def oauth_callback_legacy():
    """LEGACY (P0-8): closed. Clients must use the server-side flow.

    The previous POST endpoint accepted ``{provider, oauth_id, email,
    display_name}`` from the caller and trusted those fields. Anyone
    could forge an identity by POSTing arbitrary values. Replaced by
    the GET /oauth/start → /oauth/callback flow which holds the
    client secret on the backend and verifies the provider's
    id_token via JWKS before minting portal tokens.
    """
    return JSONResponse(
        status_code=410,
        content={
            "error_code": "endpoint_removed",
            "error_message": (
                "POST /portal/auth/oauth/callback has been removed. "
                "Use GET /portal/auth/oauth/start?provider=... to begin "
                "the server-driven OAuth flow instead."
            ),
            "replacement": "/portal/auth/oauth/start",
        },
    )


@router.get("/oauth/start")
def oauth_start(
    request: Request,
    provider: str = Query(..., pattern="^(google|github)$"),
    return_to: str = Query(default="/"),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
):
    """Begin the server-driven OAuth flow.

    Generates state + PKCE, stashes them in Redis under a 5-minute
    one-shot key, and 302s to the provider's authorize URL. The
    browser never sees our client secret; the provider never sees
    an unsafelisted return URL because we validate it here.
    """
    from app.core.config import settings
    from app.services.portal.oauth_flow_service import _provider_config

    # Whitelist the portal's own origin; extend via config later.
    allowed = [
        getattr(settings, "portal_origin", "") or "",
        getattr(settings, "admin_ui_origin", "") or "",
    ]
    allowed = [o for o in allowed if o]
    try:
        return_to = validate_return_to(return_to, allowed_origins=allowed)
    except InvalidReturnUrl as exc:
        raise ValidationError(
            f"invalid return_to: {exc}",
            code="invalid_return_to",
        )

    verifier, challenge = generate_pkce()
    state = state_store.put(
        provider=provider,
        code_verifier=verifier,
        return_to=return_to,
    )

    cfg = _provider_config(provider)
    callback_url = str(request.url_for("portal_oauth_callback"))
    from urllib.parse import urlencode
    authorize_url = (
        f"{cfg['authorize_url']}?"
        + urlencode(
            {
                "client_id": cfg["client_id"],
                "redirect_uri": callback_url,
                "response_type": "code",
                "scope": cfg["scope"],
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/oauth/callback", name="portal_oauth_callback")
def oauth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    session: Session = Depends(get_db_session),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
):
    """Finish the server-driven flow: validate state, exchange, verify, mint."""
    row = state_store.pop(state)
    if row is None:
        raise ValidationError(
            "OAuth state is missing, expired, or already used.",
            code="invalid_state",
        )
    if not code:
        raise ValidationError("Missing auth code.", code="missing_code")

    callback_url = str(request.url_for("portal_oauth_callback"))
    try:
        claims = oauth_flow_service.exchange_code_for_verified_userinfo(
            provider=row["provider"],
            code=code,
            code_verifier=row["code_verifier"],
            redirect_uri=callback_url,
        )
    except IdTokenVerificationFailed as exc:
        raise ValidationError(
            f"OAuth verification failed: {exc}",
            code="oauth_verification_failed",
        )

    result = oauth_service.login_or_create(
        session,
        provider=row["provider"],
        oauth_id=claims["sub"],
        email=claims.get("email") or "",
        display_name=claims.get("name"),
        avatar_url=claims.get("picture"),
    )
    return result


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
