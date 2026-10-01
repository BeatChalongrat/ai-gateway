"""Connector OAuth broker routes (Desktop Phase 7, PART A).

Broker model: provider tokens NEVER touch the desktop app.

1. The signed-in desktop mints a one-time CONNECT TICKET (portal-JWT
   route). The ticket bridges auth into the system browser, which has
   no portal cookies.
2. The browser opens ``/portal/connectors/oauth/start?ticket=…`` — the
   ticket IS the auth there (single-use, 5-min TTL, atomic GETDEL).
3. The standard OAuth dance runs server-side (state + PKCE S256); the
   callback stores tokens AES-256-GCM-encrypted in ``connector_tokens``
   and bounces the browser to ``sran://oauth/callback`` carrying ONLY
   the connector id + a coarse status — never tokens, never provider
   error text.
4. The mcp-gateway later fetches the user's access token via
   ``POST /{id}/token`` forwarding the user's own portal JWT; that
   endpoint refreshes first and never returns the refresh token.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_portal_user
from app.api.portal_scope import public_portal_route, scope_to_portal_tenant
from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    RateLimitExceededError,
    ValidationError,
)
from app.models.connector_token import ConnectorTokenORM
from app.models.portal_user import PortalUserORM
from app.services.portal import connector_oauth_service
from app.services.portal.connector_oauth_service import (
    CONNECTOR_PROVIDERS,
    ConnectorNotConfigured,
    OneShotStore,
    TokenExchangeFailed,
    UnknownConnector,
    connector_config,
    decrypt_token,
    encrypt_token,
    get_connector_state_store,
    get_connector_ticket_store,
    mint_ticket_token,
)
from app.services.portal.oauth_flow_service import generate_pkce


logger = logging.getLogger(__name__)

router = APIRouter()

# Desktop deep link — carries ONLY connector id + coarse status/reason.
_DEEP_LINK_BASE = "sran://oauth/callback"

# Refresh when the access token is within this skew of expiry.
_EXPIRY_SKEW_SECONDS = 60

# Simple Redis counter rate limit for the token-fetch endpoint (no
# shared per-route limiter exists in the portal routes today). Fail-open
# on Redis outage — matches the refresh-rotation posture in
# auth_service (availability over strict limiting for an authed route).
_TOKEN_RL_PREFIX = "connector_token_rl:"
_TOKEN_RL_PER_MINUTE = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes — normalize for comparison."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _row_for(
    session: Session, user_id: str, connector_id: str
) -> ConnectorTokenORM | None:
    return session.execute(
        select(ConnectorTokenORM).where(
            ConnectorTokenORM.portal_user_id == user_id,
            ConnectorTokenORM.connector_id == connector_id,
        )
    ).scalar_one_or_none()


def _deep_link_url(connector_id: str, status: str, reason: str | None = None) -> str:
    params: dict[str, str] = {"connector": connector_id, "status": status}
    if reason:
        params["reason"] = reason
    return f"{_DEEP_LINK_BASE}?{urlencode(params)}"


def _deep_link_response(
    connector_id: str, status: str, reason: str | None = None
) -> HTMLResponse:
    """Bounce the browser back to the desktop app.

    Some browsers block a plain 302 to a custom scheme, so this renders
    a page that (a) meta-refreshes to the ``sran://`` link immediately
    and (b) keeps a clickable fallback + "you can close this window".
    The URL carries only the connector id and a coarse status code —
    tokens and provider error text never appear here.
    """
    url = _deep_link_url(connector_id, status, reason)
    title = "Connected" if status == "ok" else "Connection failed"
    html = f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={url}">
<title>SRAN Desktop — {title}</title>
<style>body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#111;color:#eee}}main{{text-align:center}}a{{color:#7ab8ff}}</style>
</head><body><main>
<h1>{title}</h1>
<p>Returning to SRAN Desktop… You can close this window.</p>
<p><a href="{url}">Open SRAN Desktop</a></p>
</main></body></html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Connection state
# ---------------------------------------------------------------------------


@router.get("")
@scope_to_portal_tenant
def list_connections(
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """The caller's connector connection state — never any token material."""
    rows = session.execute(
        select(ConnectorTokenORM).where(
            ConnectorTokenORM.portal_user_id == user.id
        )
    ).scalars().all()
    return {
        "connections": [
            {
                "connector_id": r.connector_id,
                "provider": r.provider,
                "connected": True,
                "expires_at": (
                    _as_utc(r.expires_at).isoformat() if r.expires_at else None
                ),
                "scopes": r.scopes or "",
            }
            for r in rows
        ],
        # AM9: the connectors this deployment can connect (their OAuth client is configured)
        "available": connector_oauth_service.configured_connectors(),
    }


# ---------------------------------------------------------------------------
# Connect ticket (desktop → browser auth bridge)
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/ticket")
@scope_to_portal_tenant
def create_connect_ticket(
    connector_id: str,
    request: Request,
    user: PortalUserORM = Depends(get_portal_user),
    ticket_store: OneShotStore = Depends(get_connector_ticket_store),
):
    """Mint a single-use connect ticket (random 256-bit, ≤5-min TTL).

    The ticket authenticates the system browser at ``/oauth/start`` —
    the browser has no portal cookies, and the desktop must never put
    its JWT in a URL. The Redis value binds user + tenant + connector so
    the ticket can't be replayed for another connector or user.
    """
    try:
        connector_config(connector_id)
    except UnknownConnector:
        raise NotFoundError(f"Unknown connector: {connector_id}")

    ticket = ticket_store.put(
        {
            "user_id": user.id,
            "tenant_id": user.tenant_id,
            "connector_id": connector_id,
        },
        token=mint_ticket_token(),
    )
    start_url = (
        f"{request.url_for('connector_oauth_start')}?{urlencode({'ticket': ticket})}"
    )
    return {"ticket": ticket, "start_url": start_url}


# ---------------------------------------------------------------------------
# OAuth start / callback (browser-facing; ticket / state are the auth)
# ---------------------------------------------------------------------------


@router.get("/oauth/start", name="connector_oauth_start")
@public_portal_route
def oauth_start(
    request: Request,
    ticket: str = Query(...),
    ticket_store: OneShotStore = Depends(get_connector_ticket_store),
    state_store: OneShotStore = Depends(get_connector_state_store),
):
    """Consume the ticket, mint state + PKCE, 302 to the provider.

    The ticket is popped atomically (GETDEL) — a stolen start URL only
    works once, and only within the 5-minute TTL. The state row inherits
    the ticket's user/tenant/connector binding, so the callback knows
    exactly whose tokens it is storing without any cookie.
    """
    payload = ticket_store.pop(ticket)
    if payload is None:
        raise ValidationError(
            "Connect ticket is missing, expired, or already used.",
            code="invalid_ticket",
        )
    connector_id = payload.get("connector_id") or ""
    try:
        cfg = connector_config(connector_id)
        client_id, _ = connector_oauth_service.client_credentials(connector_id)
    except UnknownConnector:
        return _deep_link_response(connector_id or "unknown", "error", "unknown_connector")
    except ConnectorNotConfigured:
        return _deep_link_response(connector_id, "error", "not_configured")

    verifier, challenge = generate_pkce()
    state = state_store.put(
        {
            "user_id": payload.get("user_id"),
            "tenant_id": payload.get("tenant_id"),
            "connector_id": connector_id,
            "code_verifier": verifier,
        }
    )
    callback_url = str(request.url_for("connector_oauth_callback"))
    authorize_url = (
        f"{cfg['authorize_url']}?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": callback_url,
                "response_type": "code",
                "scope": cfg["scopes"],
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                **cfg.get("extra_authorize_params", {}),
            }
        )
    )
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/oauth/callback", name="connector_oauth_callback")
@public_portal_route
def oauth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    session: Session = Depends(get_db_session),
    state_store: OneShotStore = Depends(get_connector_state_store),
):
    """Exchange the code, store encrypted tokens, bounce to ``sran://``.

    Every failure path returns the deep-link page with a COARSE reason
    code only — provider error text never reaches a URL. State is
    one-shot (GETDEL), so a replayed callback fails closed.
    """
    row = state_store.pop(state)
    if row is None:
        return _deep_link_response("unknown", "error", "invalid_state")
    connector_id = row.get("connector_id") or "unknown"
    if error:
        # User hit "Deny" at the provider (or the provider errored).
        return _deep_link_response(connector_id, "error", "provider_denied")
    if not code:
        return _deep_link_response(connector_id, "error", "missing_code")

    callback_url = str(request.url_for("connector_oauth_callback"))
    try:
        tokens = connector_oauth_service.exchange_code_for_tokens(
            connector_id=connector_id,
            code=code,
            code_verifier=row.get("code_verifier") or "",
            redirect_uri=callback_url,
        )
    except (TokenExchangeFailed, ConnectorNotConfigured, UnknownConnector) as exc:
        logger.warning("Connector %s code exchange failed: %s", connector_id, exc)
        return _deep_link_response(connector_id, "error", "exchange_failed")

    cfg = connector_config(connector_id)
    existing = _row_for(session, row["user_id"], connector_id)
    if existing is None:
        existing = ConnectorTokenORM(
            id=str(uuid.uuid4()),
            portal_user_id=row["user_id"],
            tenant_id=row.get("tenant_id"),
            connector_id=connector_id,
            provider=cfg["provider"],
        )
        session.add(existing)
    existing.access_token_encrypted = encrypt_token(tokens["access_token"])
    # A refresh-less re-connect must not wipe a previously stored
    # refresh token (Google only returns one on the consented grant).
    if tokens.get("refresh_token"):
        existing.refresh_token_encrypted = encrypt_token(tokens["refresh_token"])
    existing.expires_at = tokens.get("expires_at")
    existing.scopes = tokens.get("scopes") or cfg["scopes"]
    session.flush()

    return _deep_link_response(connector_id, "ok")


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


@router.delete("/{connector_id}")
@scope_to_portal_tenant
def disconnect(
    connector_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Delete the stored tokens; best-effort provider-side revocation."""
    row = _row_for(session, user.id, connector_id)
    if row is None:
        raise NotFoundError(f"Connector '{connector_id}' is not connected.")

    access_token: str | None = None
    try:
        access_token = decrypt_token(row.access_token_encrypted)
    except Exception:
        pass  # undecryptable blob — still delete the row
    session.delete(row)
    session.flush()

    if access_token:
        connector_oauth_service.revoke_token_best_effort(
            connector_id=connector_id, access_token=access_token
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Token fetch (mcp-gateway, forwarding the USER'S OWN portal JWT)
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/token")
@scope_to_portal_tenant
def fetch_access_token(
    connector_id: str,
    user: PortalUserORM = Depends(get_portal_user),
    session: Session = Depends(get_db_session),
):
    """Return ``{access_token, expires_at}`` — NEVER the refresh token.

    Refreshes first when the stored token is expired or within 60s of
    expiry. 401 when the token is dead and can't be refreshed, so the
    mcp-gateway evicts the per-user instance and the desktop shows
    NotConfigured.
    """
    _rate_limit_token_fetch(user.id)

    row = _row_for(session, user.id, connector_id)
    if row is None:
        raise NotFoundError(f"Connector '{connector_id}' is not connected.")

    expires_at = _as_utc(row.expires_at)
    needs_refresh = (
        expires_at is not None
        and expires_at <= _now() + timedelta(seconds=_EXPIRY_SKEW_SECONDS)
    )
    if needs_refresh:
        if not row.refresh_token_encrypted:
            raise AuthenticationError(
                "Connector token has expired — reconnect the connector.",
                code="connector_token_expired",
            )
        try:
            refreshed = connector_oauth_service.refresh_access_token(
                connector_id=connector_id,
                refresh_token=decrypt_token(row.refresh_token_encrypted),
            )
        except (TokenExchangeFailed, ConnectorNotConfigured, UnknownConnector) as exc:
            logger.warning(
                "Connector %s token refresh failed for user %s: %s",
                connector_id,
                user.id,
                exc,
            )
            raise AuthenticationError(
                "Connector token refresh failed — reconnect the connector.",
                code="connector_refresh_failed",
            )
        row.access_token_encrypted = encrypt_token(refreshed["access_token"])
        if refreshed.get("refresh_token"):
            row.refresh_token_encrypted = encrypt_token(refreshed["refresh_token"])
        row.expires_at = refreshed.get("expires_at")
        session.flush()
        expires_at = _as_utc(row.expires_at)

    return {
        "access_token": decrypt_token(row.access_token_encrypted),
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def _rate_limit_token_fetch(user_id: str) -> None:
    """Redis INCR counter, 120/min per user; fail-open on Redis outage."""
    try:
        from app.services.admin.session_service import _build_redis_client

        client = _build_redis_client()
        key = f"{_TOKEN_RL_PREFIX}{user_id}"
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        if count > _TOKEN_RL_PER_MINUTE:
            raise RateLimitExceededError(
                "Too many connector token requests — slow down.",
            )
    except RateLimitExceededError:
        raise
    except Exception:
        pass  # Redis down — availability over strict limiting
