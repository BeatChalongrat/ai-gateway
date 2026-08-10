"""Portal auth: signup / login / refresh / logout.

These routes do NOT require a portal token. The router is mounted
without the ``get_portal_user`` dependency so unauthenticated clients
can reach them. ``/logout`` accepts a token if provided but the MVP
treats it as a client-side action (drop the token locally) — the
response is constant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.portal_scope import public_portal_route
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    RateLimitExceededError,
    ValidationError,
)
from app.services.admin.login_service import LoginRateLimiter
from app.services.admin.session_service import _build_redis_client
from app.services.portal import (
    auth_service,
    oauth_flow_service,
    oauth_service,
    password_reset_service,
)
from app.services.portal.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.services.portal.oauth_flow_service import (
    IdTokenVerificationFailed,
    InvalidReturnUrl,
    OAuthStateStore,
    OAuthTicketStore,
    generate_pkce,
    get_oauth_state_store,
    get_oauth_ticket_store,
    validate_return_to,
    verify_github_access_token,
    verify_google_id_token,
)


router = APIRouter()


OAUTH_PROVIDERS = {"google", "github"}


def _portal_redis_client():
    """FastAPI dep: reuse the admin session module's Redis factory.

    Refresh-token rotation state + password-reset tokens share this
    Redis instance under distinct key prefixes. Defined here (above the
    routes) so login/refresh/logout can depend on it.
    """
    return _build_redis_client()


# ---------------------------------------------------------------------------
# Phase 13 D — credential-route rate limiting.
#
# Mirrors the admin login gate (admin_session.py + LoginRateLimiter) with
# portal-scoped Redis key prefixes so admin and portal counters never mix.
# The limiter inherits the Phase 12 A4 posture unconditionally: on a Redis
# outage it falls back to the bounded in-process conservative window
# (~10 attempts/min per scope key) instead of failing open — these are
# credential routes, so an outage must never be a free brute-force window.
# ---------------------------------------------------------------------------

_portal_login_rate_limiter: LoginRateLimiter | None = None
_portal_signup_rate_limiter: LoginRateLimiter | None = None
_portal_oauth_verify_rate_limiter: LoginRateLimiter | None = None
_portal_oauth_ticket_rate_limiter: LoginRateLimiter | None = None


def _require_password_login_enabled() -> None:
    """Phase 0 kill-switch for the email+password surface.

    Enforced server-side (hiding the form in the frontend is not a
    gate). 403 keeps the routes discoverable-but-closed; the code path
    stays intact for a future re-enable via PASSWORD_LOGIN_ENABLED.
    """
    from app.core.config import settings

    if not settings.password_login_enabled:
        raise AuthorizationError(
            "Password login is disabled. Sign in with Google instead.",
            code="password_login_disabled",
        )


def get_portal_login_rate_limiter() -> LoginRateLimiter:
    """FastAPI dep: lazy singleton limiter for POST /portal/auth/login.

    5 failed attempts / 15 min per email — mirrors the admin gate
    (Beat's P1 locked spec); cleared on a successful login.
    30 attempts / 15 min per IP — roomier than admin's 5 because the
    portal is multi-user (office NAT / shared egress) and successful
    logins also count against the IP window; 2/min sustained still
    stops credential stuffing cold.
    """
    global _portal_login_rate_limiter
    if _portal_login_rate_limiter is None:
        _portal_login_rate_limiter = LoginRateLimiter(
            redis_client=_build_redis_client(),
            per_email_limit=5,
            per_email_window_seconds=15 * 60,
            per_ip_limit=30,
            per_ip_window_seconds=15 * 60,
            email_key_prefix="portal_login_attempts:",
            ip_key_prefix="portal_login_ip:",
        )
    return _portal_login_rate_limiter


def get_portal_signup_rate_limiter() -> LoginRateLimiter:
    """FastAPI dep: lazy singleton limiter for POST /portal/auth/signup.

    5 failed attempts / hour per email — throttles duplicate-email
    probing (the 409 is a user-existence oracle by design; this caps
    how fast it can be farmed).
    10 attempts / hour per IP, successes included — signup is a rare,
    heavyweight action (creates a tenant + grants free-trial credit),
    so 10/hour/IP caps mass account farming without bothering a real
    team onboarding a handful of users.
    """
    global _portal_signup_rate_limiter
    if _portal_signup_rate_limiter is None:
        _portal_signup_rate_limiter = LoginRateLimiter(
            redis_client=_build_redis_client(),
            per_email_limit=5,
            per_email_window_seconds=60 * 60,
            per_ip_limit=10,
            per_ip_window_seconds=60 * 60,
            email_key_prefix="portal_signup_attempts:",
            ip_key_prefix="portal_signup_ip:",
        )
    return _portal_signup_rate_limiter


def get_portal_oauth_verify_rate_limiter() -> LoginRateLimiter:
    """FastAPI dep: lazy singleton limiter for POST /portal/auth/oauth/verify.

    The endpoint is unauthenticated and each call costs a JWKS fetch +
    signature verify (Google) or two provider round-trips (GitHub), so
    it needs a brute-force bound. We can't key on email — the email is
    only known AFTER verification — so both windows are keyed on the
    client IP (the caller passes email=ip): 20 attempts / 15 min / IP,
    roomy enough for a whole office behind one NAT re-logging-in, far
    too slow for token brute-forcing.
    """
    global _portal_oauth_verify_rate_limiter
    if _portal_oauth_verify_rate_limiter is None:
        _portal_oauth_verify_rate_limiter = LoginRateLimiter(
            redis_client=_build_redis_client(),
            per_email_limit=20,
            per_email_window_seconds=15 * 60,
            per_ip_limit=20,
            per_ip_window_seconds=15 * 60,
            email_key_prefix="portal_oauth_verify:",
            ip_key_prefix="portal_oauth_verify_ip:",
        )
    return _portal_oauth_verify_rate_limiter


def get_portal_oauth_ticket_rate_limiter() -> LoginRateLimiter:
    """FastAPI dep: lazy singleton limiter for POST /oauth/desktop/exchange.

    Tickets are 128-bit-entropy one-shots with a 60s TTL, so online
    guessing is already hopeless — the limiter exists to bound abuse
    of the unauthenticated endpoint itself (Redis churn, log noise).
    Keyed on IP like the verify limiter (no email until redemption):
    20 attempts / 15 min / IP, failures only (a successful redeem
    clears the window).
    """
    global _portal_oauth_ticket_rate_limiter
    if _portal_oauth_ticket_rate_limiter is None:
        _portal_oauth_ticket_rate_limiter = LoginRateLimiter(
            redis_client=_build_redis_client(),
            per_email_limit=20,
            per_email_window_seconds=15 * 60,
            per_ip_limit=20,
            per_ip_window_seconds=15 * 60,
            email_key_prefix="portal_oauth_ticket:",
            ip_key_prefix="portal_oauth_ticket_ip:",
        )
    return _portal_oauth_ticket_rate_limiter


def _client_ip(request: Request) -> str:
    """Same trust model as admin login (admin_session._client_ip):
    first X-Forwarded-For hop when a reverse proxy fronts us, else the
    direct peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _raise_429(decision, *, code: str) -> None:
    raise RateLimitExceededError(
        "Too many attempts. Try again later.",
        code=code,
        details={
            "retry_after_seconds": decision.retry_after_seconds,
            "scope": decision.scope or "unknown",
        },
    )


def _auth_fail_response(request: Request, message: str, code: str) -> JSONResponse:
    """401 envelope that also clears both auth cookies.

    Matches the body shape produced by ``error_handler._build_body`` so
    clients see a consistent error contract, while expiring the stale
    cookies so the browser stops re-sending a dead session.
    """
    request_id = getattr(request.state, "request_id", None)
    resp = JSONResponse(
        status_code=401,
        content={
            "error": {"code": code, "message": message},
            "request_id": request_id,
        },
    )
    clear_auth_cookies(resp)
    return resp


def _session_body(tokens: dict) -> dict:
    """Strip tokens from a service result — only user info goes in the body.

    Tokens now travel exclusively in HttpOnly cookies (set by the route);
    the JSON body never carries them.
    """
    return {"user": tokens.get("user")}


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    # Phase 3 — optional tenancy inputs. Either provide an invite
    # code (join an existing tenant as member) or a tenant name
    # (create a new tenant as owner). Omit both and a default tenant
    # name is derived from the email.
    invite_code: str | None = Field(default=None, max_length=128)
    tenant_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    # Optional now — browsers send the refresh token via the HttpOnly
    # cookie. The body field is kept for non-browser API clients (mobile)
    # that can't use cookies.
    refresh_token: str | None = None


class CookieExchangeRequest(BaseModel):
    # Used by the OAuth completion page to convert the tokens it holds
    # (from the NextAuth session) into HttpOnly cookies the browser can't
    # read. The tokens are proof in themselves — only the backend mints
    # valid portal tokens — so no extra auth is needed.
    access_token: str = Field(..., min_length=10, max_length=8192)
    refresh_token: str = Field(..., min_length=10, max_length=8192)


@router.post("/signup")
@public_portal_route
def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    rate_limiter: LoginRateLimiter = Depends(get_portal_signup_rate_limiter),
):
    _require_password_login_enabled()
    # Rate-limit BEFORE any DB/bcrypt work (Phase 13 D) — same order as
    # admin login. Keyed on the normalised email + client IP.
    email_norm = (body.email or "").strip().lower()
    ip = _client_ip(request)
    decision = rate_limiter.check(email=email_norm, ip=ip)
    if not decision.allowed:
        _raise_429(decision, code="signup_rate_limited")

    try:
        tokens = auth_service.signup(
            session,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            invite_code=body.invite_code,
            tenant_name=body.tenant_name,
        )
    except (ConflictError, ValidationError):
        # Failed signup (duplicate email, weak password, bad invite…)
        # counts against both windows — throttles enumeration via the
        # 409/422 responses.
        rate_limiter.record_failure(email=email_norm, ip=ip)
        raise
    # Success clears the per-email counter but still bumps the per-IP
    # window — mass account creation from one IP is the abuse vector.
    rate_limiter.record_success(email=email_norm, ip=ip)
    # Auto-login after signup (preserves prior behaviour) — set cookies.
    set_auth_cookies(
        response,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )
    return _session_body(tokens)


@router.post("/login")
@public_portal_route
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    rate_limiter: LoginRateLimiter = Depends(get_portal_login_rate_limiter),
):
    _require_password_login_enabled()
    # Phase 13 D — mirror of the admin login gate (admin_session.py):
    # check BEFORE bcrypt so attackers can't spend our CPU, record the
    # outcome after. Keyed on the normalised email + client IP.
    email_norm = (body.email or "").strip().lower()
    ip = _client_ip(request)
    decision = rate_limiter.check(email=email_norm, ip=ip)
    if not decision.allowed:
        _raise_429(decision, code="login_rate_limited")

    try:
        tokens = auth_service.login(session, email=body.email, password=body.password)
    except AuthenticationError:
        rate_limiter.record_failure(email=email_norm, ip=ip)
        raise
    except AuthorizationError:
        # email_not_verified fires AFTER the password verified — the
        # caller proved the credential, so this isn't a guessing signal.
        # Treat like a success for the limiter (clears the email window,
        # still bumps the IP window).
        rate_limiter.record_success(email=email_norm, ip=ip)
        raise
    rate_limiter.record_success(email=email_norm, ip=ip)
    set_auth_cookies(
        response,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )
    return _session_body(tokens)


@router.post("/refresh")
@public_portal_route
def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    session: Session = Depends(get_db_session),
    redis_client=Depends(_portal_redis_client),
):
    """Rotate the session: mint a new access + refresh pair, set cookies.

    Reads the refresh token from the HttpOnly cookie (browsers) and falls
    back to the request body (non-browser API clients). On any failure the
    response clears both cookies so the client stops re-sending a dead
    session and bounces to login.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if not token and body is not None:
        token = body.refresh_token
    if not token:
        return _auth_fail_response(
            request, "Missing refresh token.", "missing_refresh_token"
        )
    try:
        tokens = auth_service.refresh(
            session, refresh_token=token, redis_client=redis_client
        )
    except AuthenticationError as exc:
        return _auth_fail_response(
            request, exc.message, getattr(exc, "code", "authentication_error")
        )
    set_auth_cookies(
        response,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )
    return _session_body(tokens)


@router.post("/cookie-exchange")
@public_portal_route
def cookie_exchange(
    body: CookieExchangeRequest,
    request: Request,
    response: Response,
):
    """Convert a portal token pair into HttpOnly cookies (OAuth bootstrap).

    The portal's NextAuth OAuth flow ends with the access + refresh tokens
    held in the NextAuth session (server-issued). The browser posts them
    here once so we can re-issue them as HttpOnly cookies — after this the
    OAuth session is indistinguishable from a password login and JS never
    needs to touch the tokens again.

    The tokens are self-authenticating: we verify both signatures + types
    (only the backend's secret can mint a valid portal token), so a
    forged body can't establish a session.
    """
    try:
        acc = auth_service.verify_portal_token(body.access_token)
        ref = auth_service.verify_portal_token(body.refresh_token)
    except Exception:
        return _auth_fail_response(
            request, "Invalid tokens.", "invalid_tokens"
        )
    if acc.get("token_use") not in (None, "access") or ref.get("token_use") != "refresh":
        return _auth_fail_response(
            request, "Token role mismatch.", "invalid_tokens"
        )
    if acc.get("sub") != ref.get("sub") or not acc.get("sub"):
        return _auth_fail_response(
            request, "Token subject mismatch.", "invalid_tokens"
        )
    set_auth_cookies(
        response,
        access_token=body.access_token,
        refresh_token=body.refresh_token,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Phase 2C Commit 14 — email verification flow
# ---------------------------------------------------------------------------


class VerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=4096)


@router.post("/request-verification-email")
@public_portal_route
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
@public_portal_route
def verify_email(
    body: VerifyEmailRequest,
    session: Session = Depends(get_db_session),
):
    """Consume a verification token and flip the ``email_verified`` flag."""
    return auth_service.verify_email(session, token=body.token)


# ---------------------------------------------------------------------------
# Phase 3.5 Commit 32 — password reset flow
# ---------------------------------------------------------------------------


class PasswordResetRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)


@router.post("/password-reset/request")
@public_portal_route
def password_reset_request(
    body: PasswordResetRequest,
    session: Session = Depends(get_db_session),
    redis_client=Depends(_portal_redis_client),
):
    """Start a reset: generate a token + email the link.

    Always returns ``{"ok": True}`` even if the email is unknown —
    no user-existence oracle. Dev/test mode echoes the token so
    integration tests don't need a mail server.
    """
    return password_reset_service.request_reset(
        session, redis_client, email=body.email
    )


@router.post("/password-reset/confirm")
@public_portal_route
def password_reset_confirm(
    body: PasswordResetConfirmRequest,
    session: Session = Depends(get_db_session),
    redis_client=Depends(_portal_redis_client),
):
    """Consume a reset token + set a new password.

    On success, every live portal JWT for the user is invalidated
    via the token_version bump (Phase 3 Concern 1).
    """
    return password_reset_service.confirm_reset(
        session,
        redis_client,
        token=body.token,
        new_password=body.new_password,
    )


@router.post("/logout")
@public_portal_route
def logout(
    request: Request,
    response: Response,
    redis_client=Depends(_portal_redis_client),
):
    """Real logout: blacklist the refresh token's jti + clear cookies.

    The refresh token's ``jti`` is added to the Redis rotation blacklist
    so it can never mint another access token, then both HttpOnly cookies
    are expired. Idempotent — a missing cookie still returns 200.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        auth_service.revoke_refresh_token(token, redis_client=redis_client)
    clear_auth_cookies(response)
    return {"message": "ok"}


@router.post("/oauth/callback", status_code=410)
@public_portal_route
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


class OAuthVerifyRequest(BaseModel):
    """Body for ``POST /portal/auth/oauth/verify``.

    Exactly one of ``id_token`` or ``access_token`` should be set:
    Google issues an OIDC id_token (verified against JWKS), GitHub
    only an access_token (verified by calling /user). Whatever the
    caller sends will be verified by the provider, not trusted as-is.
    """

    provider: str = Field(..., pattern="^(google|github)$")
    id_token: str | None = Field(default=None, max_length=8192)
    access_token: str | None = Field(default=None, max_length=8192)


@router.post("/oauth/verify")
@public_portal_route
def oauth_verify(
    payload: OAuthVerifyRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    rate_limiter: LoginRateLimiter = Depends(get_portal_oauth_verify_rate_limiter),
):
    """Verify a provider-issued token server-side and mint portal tokens.

    Used by the portal's NextAuth ``signIn`` callback. NextAuth performs
    the user-facing OAuth dance, then forwards the verified provider
    token here. The backend re-verifies the token (the entire point of
    this endpoint — never trust the caller's claim about who the user
    is) and only then calls ``oauth_service.login_or_create``.

    Closes the security hole from the legacy POST callback (P0-8) by
    requiring proof-of-identity from the provider, not from the caller.
    """
    # Rate limit BEFORE any provider round-trip. Email isn't known yet
    # (it comes out of the verified token), so both windows key on IP.
    ip = _client_ip(request)
    decision = rate_limiter.check(email=ip, ip=ip)
    if not decision.allowed:
        _raise_429(decision, code="oauth_verify_rate_limited")

    try:
        if payload.provider == "google":
            if not payload.id_token:
                raise ValidationError(
                    "id_token is required for Google verify.",
                    code="missing_id_token",
                )
            claims = verify_google_id_token(payload.id_token)
        else:  # github
            if not payload.access_token:
                raise ValidationError(
                    "access_token is required for GitHub verify.",
                    code="missing_access_token",
                )
            claims = verify_github_access_token(payload.access_token)
    except IdTokenVerificationFailed as exc:
        rate_limiter.record_failure(email=ip, ip=ip)
        raise ValidationError(
            f"OAuth verification failed: {exc}",
            code="oauth_verification_failed",
        )
    rate_limiter.record_success(email=ip, ip=ip)

    return oauth_service.login_or_create(
        session,
        provider=payload.provider,
        oauth_id=claims["sub"],
        email=claims.get("email") or "",
        display_name=claims.get("name"),
        avatar_url=claims.get("picture"),
        email_verified=bool(claims.get("email_verified")),
    )


@router.get("/oauth/start")
@public_portal_route
def oauth_start(
    request: Request,
    provider: str = Query(..., pattern="^(google|github)$"),
    return_to: str = Query(default="/"),
    client: str = Query(default="web", pattern="^(web|desktop)$"),
    nonce: str | None = Query(default=None, max_length=128),
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

    # SSO Phase 2 (B0): the desktop hand-off ticket is nonce-bound.
    # ``sran://`` is claimable by any local app, so a hijacked deep
    # link alone must not be redeemable — the redeeming process has to
    # present the nonce it minted before opening the browser. Desktop
    # starts therefore REQUIRE a well-formed nonce; the web path
    # ignores one entirely (its result never rides a deep link).
    import re as _re

    if client == "desktop":
        if not nonce or not _re.fullmatch(r"[A-Za-z0-9_-]{22,128}", nonce):
            raise ValidationError(
                "Desktop OAuth requires a nonce of 22-128 URL-safe "
                "base64 characters.",
                code="invalid_nonce",
            )
    else:
        nonce = None

    verifier, challenge = generate_pkce()
    state = state_store.put(
        provider=provider,
        code_verifier=verifier,
        return_to=return_to,
        # SSO Phase 2: "desktop" routes the callback result through the
        # one-time-ticket deep link instead of the web response.
        client=client,
        nonce=nonce,
    )

    cfg = _provider_config(provider)
    callback_url = str(request.url_for("portal_oauth_callback"))
    from urllib.parse import urlencode
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # UX hint only: pre-filters Google's account chooser to the
    # workspace domain. Enforcement stays server-side in
    # login_or_create (email domain + email_verified) — a client can
    # strip this param and gains nothing.
    if provider == "google" and (settings.google_hosted_domain or "").strip():
        params["hd"] = settings.google_hosted_domain.strip()
    authorize_url = f"{cfg['authorize_url']}?" + urlencode(params)
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/oauth/callback", name="portal_oauth_callback")
@public_portal_route
def oauth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    session: Session = Depends(get_db_session),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    ticket_store: OAuthTicketStore = Depends(get_oauth_ticket_store),
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
        email_verified=bool(claims.get("email_verified")),
    )

    # SSO Phase 2 — desktop hand-off. The system browser can't deliver
    # tokens to the app, so we park the login result under a one-time
    # 60s ticket and deep-link ONLY the opaque ticket; the app redeems
    # it via POST /oauth/desktop/exchange. Tokens must never ride the
    # sran:// URL itself (custom schemes are claimable by any local app
    # and their URLs persist in shell/browser history).
    if row.get("client") == "desktop":
        # B0: bind the ticket to the nonce the desktop app minted at
        # /oauth/start — redemption requires presenting it again.
        ticket = ticket_store.put(result, nonce=row.get("nonce") or "")
        return RedirectResponse(
            url=f"sran://oauth/callback?ticket={ticket}", status_code=302
        )

    return result


class DesktopExchangeRequest(BaseModel):
    """Body for ``POST /portal/auth/oauth/desktop/exchange``."""

    ticket: str = Field(..., min_length=16, max_length=256)
    # B0: the nonce the app minted before opening the browser. A
    # hijacked sran:// link carries only the ticket — without the
    # nonce it cannot be redeemed.
    nonce: str = Field(..., min_length=22, max_length=128)


@router.post("/oauth/desktop/exchange")
@public_portal_route
def oauth_desktop_exchange(
    payload: DesktopExchangeRequest,
    request: Request,
    rate_limiter: LoginRateLimiter = Depends(get_portal_oauth_ticket_rate_limiter),
    ticket_store: OAuthTicketStore = Depends(get_oauth_ticket_store),
):
    """Redeem the one-time desktop OAuth ticket for portal tokens.

    Counterpart of the ``sran://oauth/callback?ticket=...`` deep link:
    the desktop app re-enters here over HTTPS and swaps the opaque
    ticket for ``{access_token, refresh_token, user}``. The ticket is
    a 60s one-shot (atomic GETDEL) — expired, unknown, and replayed
    tickets are indistinguishable by design (no oracle) and all yield
    the same 401. Tokens are returned in the BODY, not cookies: the
    desktop stores them itself (DPAPI), it has no cookie jar.
    """
    ip = _client_ip(request)
    decision = rate_limiter.check(email=ip, ip=ip)
    if not decision.allowed:
        _raise_429(decision, code="oauth_ticket_rate_limited")

    entry = ticket_store.pop(payload.ticket)
    # One 401 for unknown/expired/replayed/nonce-mismatched tickets —
    # deliberately indistinguishable (no oracle). A wrong-nonce attempt
    # also BURNS the ticket (pop is one-shot), so a hijacker who races
    # the real app destroys the login instead of winning it.
    import hmac as _hmac

    if entry is None or not _hmac.compare_digest(
        entry.get("nonce") or "", payload.nonce
    ):
        rate_limiter.record_failure(email=ip, ip=ip)
        raise AuthenticationError(
            "OAuth ticket is invalid, expired, or already used.",
            code="invalid_ticket",
        )
    rate_limiter.record_success(email=ip, ip=ip)
    return entry["result"]


@router.get("/public-settings")
@public_portal_route
def public_settings(session: Session = Depends(get_db_session)):
    """Public values safe to read unauthenticated.

    Used by the signup page to render the correct "Get X free tokens"
    label and to hide the form entirely when signups are disabled.
    Intentionally scoped — never expose the full ``system_settings``
    table here.
    """
    from app.core.config import settings
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
        "resend_configured": bool((settings.resend_api_key or "").strip()),
        # Phase 0 — lets the login/signup pages hide the password form
        # when the server has the surface disabled. The 403 gate on
        # /login + /signup is the actual enforcement; this is UI truth.
        "password_login_enabled": bool(settings.password_login_enabled),
    }


@router.get("/invite/{code}")
@public_portal_route
def preview_invite(code: str, session: Session = Depends(get_db_session)):
    """Pre-signup peek at an invite code — public, read-only.

    The invite landing page (portal ``/invite/:code``) renders a
    context banner ("You're joining Acme Corp as Member") before the
    user signs up, so the CTA is less scary than a bare signup form.
    We return a small subset of the invite row — tenant name, role,
    inviter display name, expiry — or ``{ valid: false }`` for
    missing/revoked/expired codes. The opaque false is deliberate:
    we do not want to enumerate live codes.
    """
    from app.models.tenant import TenantORM
    from app.services.portal import tenant_service

    invite = tenant_service.preview_invite(session, code=code)
    if invite is None:
        return {"valid": False}

    tenant = session.get(TenantORM, invite.tenant_id)
    tenant_name = tenant.name if tenant is not None else "a team"
    # Inviter display name lookup is a bonus — if the row was hard-
    # deleted for some reason we still want the landing page to work.
    inviter = None
    if invite.created_by:
        from app.models.portal_user import PortalUserORM

        row = session.get(PortalUserORM, invite.created_by)
        if row is not None:
            inviter = row.display_name or row.email
    return {
        "valid": True,
        "tenant_name": tenant_name,
        "tenant_id": invite.tenant_id,
        "role": invite.role or "member",
        "inviter_name": inviter,
        "expires_at": invite.expires_at.isoformat()
        if invite.expires_at
        else None,
    }
