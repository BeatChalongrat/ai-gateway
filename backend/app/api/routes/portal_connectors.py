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


# Which client opened the consent page. An ALLOW-LIST carried by the ticket
# and then the state — never read from the callback's own query. ``web`` is a
# browser tab of the web chat: it has no app to return to, so its page never
# names the desktop app and carries no ``sran://`` link. ``desktop`` is the
# desktop app, which opens consent in the system browser: the page sends the
# person back to the app. A caller that says nothing is an older desktop.
RETURN_CLIENTS = ("web", "desktop")
_DEFAULT_CLIENT = "desktop"

_RETURN_LANGS = ("th", "en")

_RETURN_PAGE = """<!doctype html>
<html lang="{html_lang}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
{refresh}<title>SRAN — {title}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--line:#e3e6eb;--ink:#17181c;--soft:#5b6270;--accent:#1d4ed8;--on:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111318;--card:#17181c;--line:#2a2d35;--ink:#eef0f4;--soft:#a3abb9;--accent:#8fb0ff;--on:#0b1020}}}}
body{{font-family:'Segoe UI','Sarabun',system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:var(--bg);color:var(--ink)}}
main{{text-align:center;max-width:440px;margin:16px;padding:32px 28px;background:var(--card);border:1px solid var(--line);border-radius:16px}}
h1{{font-size:20px;margin:0 0 6px}}h2{{font-size:14px;font-weight:400;color:var(--soft);margin:0 0 16px}}
p{{font-size:14px;line-height:1.6;color:var(--soft);margin:0 0 12px}}
a{{color:var(--accent)}}a.go{{display:inline-block;margin-top:8px;padding:10px 18px;border-radius:10px;background:var(--accent);color:var(--on);text-decoration:none;font-weight:600}}
small{{font-size:12px;color:var(--soft)}}
</style>
</head><body data-client="{client}" data-status="{status}"><main>
{body}</main></body></html>"""

_TITLES = {"ok": ("เชื่อมต่อแล้ว", "Connected"), "error": ("เชื่อมต่อไม่สำเร็จ", "Connection failed")}
# What to do next, per client and outcome (Thai, English) — for a caller that
# named no language (an older client): both are shown, as before.
_NEXT = {
    ("web", "ok"): ("ปิดแท็บนี้ แล้วกลับไปที่แท็บ SRAN Chat ที่เปิดค้างไว้ จากนั้นกด “ตรวจอีกครั้ง”",
                    "Close this tab and go back to your SRAN Chat tab, then press “Check again”."),
    ("web", "error"): ("ยังไม่ได้เชื่อมต่อ — ปิดแท็บนี้ แล้วกลับไปที่แท็บ SRAN Chat เพื่อกดเชื่อมต่ออีกครั้ง",
                       "Nothing was connected. Close this tab and go back to your SRAN Chat tab to try again."),
    ("desktop", "ok"): ("กำลังกลับไปที่แอป SRAN Desktop — ปิดหน้าต่างนี้ได้เลย",
                        "Returning to the SRAN Desktop app. You can close this window."),
    ("desktop", "error"): ("ยังไม่ได้เชื่อมต่อ — ปิดหน้าต่างนี้ แล้วลองเชื่อมต่ออีกครั้งจากแอป SRAN Desktop",
                           "Nothing was connected. Close this window and try again from the SRAN Desktop app."),
    # The state is gone (an old or replayed link), so nobody knows which client asked.
    ("unknown", "error"): ("ลิงก์นี้หมดอายุหรือถูกใช้ไปแล้ว — ปิดหน้านี้ แล้วกดเชื่อมต่ออีกครั้งจาก SRAN",
                           "This link has expired or was already used. Close this page and connect again from SRAN."),
}

# The same, for a client that says which language its screen is in: one
# language, and what really happens next. The web tab the person came from
# learns by itself that the connection is there (it looks again when it is
# shown), so nothing has to be pressed; with a way back the page offers it,
# without one it says what to do with this tab. The desktop app opens its
# Connectors page when the link reaches it.
_SAID = {
    "th": {
        "title": {"ok": "เชื่อมต่อแล้ว", "error": "ยังไม่ได้เชื่อมต่อ"},
        ("web", "ok"): "กลับไปที่ SRAN Chat เพื่อทำต่อได้เลย แท็บที่เปิดค้างไว้จะรู้เองว่าเชื่อมต่อแล้ว ไม่ต้องกดอะไรเพิ่ม",
        ("web", "error"): "ยังไม่มีอะไรถูกเปลี่ยน กลับไปที่ SRAN Chat แล้วกดเชื่อมต่ออีกครั้งได้ทุกเมื่อ",
        ("desktop", "ok"): "แอป SRAN Desktop กำลังเปิดหน้า Connectors ให้ ปิดหน้าต่างนี้ได้เลย",
        ("desktop", "error"): "ยังไม่มีอะไรถูกเปลี่ยน แอป SRAN Desktop กำลังเปิดหน้า Connectors ให้ลองอีกครั้ง ปิดหน้าต่างนี้ได้เลย",
        ("unknown", "error"): "ลิงก์นี้หมดอายุหรือถูกใช้ไปแล้ว ปิดหน้านี้ แล้วกดเชื่อมต่ออีกครั้งจาก SRAN",
        "back": "กลับไปที่ SRAN Chat",
        "close": "ปิดแท็บนี้ แล้วกลับไปที่แท็บ SRAN Chat ที่เปิดค้างไว้",
        "open": "เปิด SRAN Desktop",
    },
    "en": {
        "title": {"ok": "Connected", "error": "Nothing was connected"},
        ("web", "ok"): "Go back to SRAN Chat to carry on. The tab you came from sees the connection by itself; there is nothing more to press.",
        ("web", "error"): "Nothing has changed. Go back to SRAN Chat and connect again whenever you like.",
        ("desktop", "ok"): "The SRAN Desktop app is opening its Connectors page. You can close this window.",
        ("desktop", "error"): "Nothing has changed. The SRAN Desktop app is opening its Connectors page so you can try again. You can close this window.",
        ("unknown", "error"): "This link has expired or was already used. Close this page and connect again from SRAN.",
        "back": "Back to SRAN Chat",
        "close": "Close this tab and go back to your SRAN Chat tab.",
        "open": "Open SRAN Desktop",
    },
}


def _return_client(value: object) -> str:
    return value if value in RETURN_CLIENTS else _DEFAULT_CLIENT


def _return_lang(value: object) -> str:
    return value if value in _RETURN_LANGS else ""


def _safe_return_to(value: object, origin: str | None) -> str:
    """Where the web page may send the person back to: a place of the web chat
    itself, on the origin that asked for the ticket — or nowhere.

    The value came from a client and ends up as a link on a page of ours, so
    it is taken only when all of this holds: http(s); no credentials; its
    origin is the request's own ``Origin`` AND one this deployment serves
    (``cors_origins``); its path is under ``/chat``; nothing in it needs
    escaping to sit inside an attribute. Anything else is no link at all.
    """
    from urllib.parse import urlsplit

    from app.core.config import settings

    if not isinstance(value, str) or not value or len(value) > 300 or not origin:
        return ""
    if any(ch in value for ch in '"<>\'`\\ \t\r\n'):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        return ""
    theirs = f"{parts.scheme}://{parts.netloc}"
    if theirs != origin.rstrip("/") or theirs not in {o.rstrip("/") for o in settings.cors_origins_list}:
        return ""
    if parts.path != "/chat" and not parts.path.startswith("/chat/"):
        return ""
    return value


def _return_response(
    client: str,
    connector_id: str,
    status: str,
    reason: str | None = None,
    *,
    lang: str = "",
    return_to: str = "",
) -> HTMLResponse:
    """The page the consent flow ends on, written for the person who began it.

    ``web``: the person is in a browser tab the web chat opened — the page
    says so, has no desktop link at all, and offers the way back to where
    they were (``return_to``, already checked by ``_safe_return_to``).
    ``desktop``: the page bounces to the app, which opens its Connectors
    page. Some browsers block a plain 302 to a custom scheme, so it (a)
    meta-refreshes to the ``sran://`` link immediately and (b) keeps a
    clickable fallback. ``unknown`` (no state row to say who asked): neutral
    words, the app link only as a fallback.

    ``lang`` is the language of the screen that asked: the page is in that
    one language. A caller that named none gets Thai and English together,
    as every page did before. Either way the page — and the link — carries
    only the connector id and a coarse status code; tokens and provider
    error text never appear here.
    """
    from html import escape

    status = "ok" if status == "ok" else "error"
    lang = _return_lang(lang)
    refresh = link = ""
    url = _deep_link_url(connector_id, status, reason) if client != "web" else ""
    if client == "desktop":
        refresh = f'<meta http-equiv="refresh" content="0;url={escape(url, quote=True)}">\n'
    if lang:
        said = _SAID[lang]
        title = said["title"][status]
        sentence = said.get((client, status)) or said[("unknown", "error")]
        body = f"<h1>{title}</h1>\n<p>{sentence}</p>\n"
        if client == "web":
            if return_to:
                body += f'<p><a class="go" href="{escape(return_to, quote=True)}">{said["back"]}</a></p>\n'
            else:
                body += f'<p>{said["close"]}</p>\n'
            if reason:
                body += f"<p><small>({escape(reason)})</small></p>\n"
        else:
            body += f'<p><a href="{escape(url, quote=True)}">{said["open"]}</a></p>\n'
        return HTMLResponse(_RETURN_PAGE.format(html_lang=lang, refresh=refresh, title=title, body=body,
                                                client=client, status=status))
    title_th, title_en = _TITLES[status]
    next_th, next_en = _NEXT.get((client, status)) or _NEXT[("unknown", "error")]
    if client != "web":
        link = f'<p><a href="{url}">เปิด SRAN Desktop · Open SRAN Desktop</a></p>\n'
    elif reason:
        # No link to carry it: the coarse reason stays readable for support, nothing else.
        link = f"<p><small>({reason})</small></p>\n"
    body = f"<h1>{title_th}</h1>\n<h2>{title_en}</h2>\n<p>{next_th}</p>\n<p>{next_en}</p>\n{link}"
    return HTMLResponse(_RETURN_PAGE.format(html_lang="th", refresh=refresh, title=title_en, body=body,
                                            client=client, status=status))


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
    client: str = Query(default=_DEFAULT_CLIENT, pattern="^(web|desktop)$"),
    lang: str = Query(default="", pattern="^(th|en)?$"),
    return_to: str = Query(default="", max_length=300),
    user: PortalUserORM = Depends(get_portal_user),
    ticket_store: OneShotStore = Depends(get_connector_ticket_store),
):
    """Mint a single-use connect ticket (random 256-bit, ≤5-min TTL).

    The ticket authenticates the system browser at ``/oauth/start`` —
    the browser has no portal cookies, and the desktop must never put
    its JWT in a URL. The Redis value binds user + tenant + connector so
    the ticket can't be replayed for another connector or user.

    ``client`` says who is asking (``web``: a browser tab of the web chat;
    ``desktop``: the desktop app, the default for callers that say nothing).
    It rides the ticket and the state so the page the flow ends on is
    written for that client. ``lang`` is the language of the screen that
    asks (none = an older client: the page says it in both), and
    ``return_to`` — the web chat only — is where the person was, offered
    as the way back when it is a place of the web chat on the origin that
    asked (``_safe_return_to``).
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
            "client": client,
            "lang": lang,
            "return_to": _safe_return_to(return_to, request.headers.get("origin")) if client == "web" else "",
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
    client = _return_client(payload.get("client"))
    lang, return_to = _return_lang(payload.get("lang")), str(payload.get("return_to") or "")
    try:
        cfg = connector_config(connector_id)
        client_id, _ = connector_oauth_service.client_credentials(connector_id)
    except UnknownConnector:
        return _return_response(client, connector_id or "unknown", "error", "unknown_connector",
                                lang=lang, return_to=return_to)
    except ConnectorNotConfigured:
        return _return_response(client, connector_id, "error", "not_configured", lang=lang, return_to=return_to)

    verifier, challenge = generate_pkce()
    state = state_store.put(
        {
            "user_id": payload.get("user_id"),
            "tenant_id": payload.get("tenant_id"),
            "connector_id": connector_id,
            "code_verifier": verifier,
            "client": client,
            "lang": lang,
            "return_to": return_to,
        }
    )
    callback_url = str(request.url_for("connector_oauth_callback"))
    authorize_url = (
        f"{connector_oauth_service.endpoint(connector_id, 'authorize_url')}?"
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
    """Exchange the code, store encrypted tokens, end on the client's own page.

    The page is written for the client that began the flow (``web`` or
    ``desktop``, from the state — see ``_return_response``). Every failure
    path returns it with a COARSE reason code only — provider error text
    never reaches a URL or the page. State is one-shot (GETDEL), so a
    replayed callback fails closed.
    """
    row = state_store.pop(state)
    if row is None:
        return _return_response("unknown", "unknown", "error", "invalid_state")
    connector_id = row.get("connector_id") or "unknown"
    client = _return_client(row.get("client"))
    # the state was written by /oauth/start from a ticket whose return_to was checked when it was minted
    said = {"lang": _return_lang(row.get("lang")), "return_to": str(row.get("return_to") or "") if client == "web" else ""}
    if error:
        # User hit "Deny" at the provider (or the provider errored).
        return _return_response(client, connector_id, "error", "provider_denied", **said)
    if not code:
        return _return_response(client, connector_id, "error", "missing_code", **said)

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
        return _return_response(client, connector_id, "error", "exchange_failed", **said)

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

    return _return_response(client, connector_id, "ok", **said)


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
