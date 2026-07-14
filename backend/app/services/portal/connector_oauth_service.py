"""Connector OAuth broker (Desktop Phase 7, PART A).

Server-side OAuth for MCP CONNECTORS (gmail / gdrive / gcal / slack /
github) — deliberately separate from the portal LOGIN flow in
``oauth_flow_service``: login proves who the user is; this brokers
long-lived provider tokens the per-user MCP servers act with. Provider
tokens exist only encrypted in ``connector_tokens`` and in the memory /
env of the per-user MCP server instance — never in a URL, never on the
desktop, never logged.

Pieces:

* ``CONNECTOR_PROVIDERS`` — config-only registry keyed by CONNECTOR id
  (not provider) because scopes differ per connector on the same
  provider (gmail vs gdrive vs gcal are all Google). Adding a connector
  is a new entry + client-credential settings, no code.

* ``OneShotStore`` — Redis-backed, TTL'd, atomic-GETDEL store (the
  ``OAuthStateStore`` semantics with a configurable key prefix). Used
  twice: connect tickets (``connector_ticket:``, the desktop→browser
  auth bridge) and OAuth state (``connector_oauth_state:``).

* Exchange / refresh / revoke helpers — thin httpx wrappers that tests
  monkeypatch whole, mirroring ``exchange_code_for_verified_userinfo``.

Redis keys used:
  ``connector_ticket:<token>``      — JSON, EX=300; GETDEL on consume.
  ``connector_oauth_state:<uuid>``  — JSON, EX=300; GETDEL on consume.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UnknownConnector(Exception):
    """connector_id is not in CONNECTOR_PROVIDERS."""


class ConnectorNotConfigured(Exception):
    """The provider's client id/secret settings are empty."""


class TokenExchangeFailed(Exception):
    """The provider rejected the code exchange / refresh call."""


# ---------------------------------------------------------------------------
# Provider registry — keyed by CONNECTOR id (scopes differ per connector)
# ---------------------------------------------------------------------------

_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"

CONNECTOR_PROVIDERS: dict[str, dict[str, Any]] = {
    "gmail": {
        "provider": "google",
        "authorize_url": _GOOGLE_AUTH,
        "token_url": _GOOGLE_TOKEN,
        "revoke_url": _GOOGLE_REVOKE,
        "scopes": "https://www.googleapis.com/auth/gmail.modify",
        "supports_refresh": True,
        # offline access is what makes Google return a refresh_token.
        "extra_authorize_params": {"access_type": "offline", "prompt": "consent"},
        "settings_prefix": "connector_oauth_google",
    },
    "gdrive": {
        "provider": "google",
        "authorize_url": _GOOGLE_AUTH,
        "token_url": _GOOGLE_TOKEN,
        "revoke_url": _GOOGLE_REVOKE,
        "scopes": "https://www.googleapis.com/auth/drive.readonly",
        "supports_refresh": True,
        "extra_authorize_params": {"access_type": "offline", "prompt": "consent"},
        "settings_prefix": "connector_oauth_google",
    },
    "gcal": {
        "provider": "google",
        "authorize_url": _GOOGLE_AUTH,
        "token_url": _GOOGLE_TOKEN,
        "revoke_url": _GOOGLE_REVOKE,
        "scopes": "https://www.googleapis.com/auth/calendar",
        "supports_refresh": True,
        "extra_authorize_params": {"access_type": "offline", "prompt": "consent"},
        "settings_prefix": "connector_oauth_google",
    },
    "slack": {
        "provider": "slack",
        "authorize_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "revoke_url": "https://slack.com/api/auth.revoke",
        # Bot scopes for @modelcontextprotocol/server-slack's tool set.
        "scopes": "channels:history,channels:read,chat:write,reactions:write,users:read",
        # Slack tokens only rotate when the app opts into token rotation;
        # plain bot tokens don't expire — no refresh cycle.
        "supports_refresh": False,
        "extra_authorize_params": {},
        "settings_prefix": "connector_oauth_slack",
    },
    "github": {
        "provider": "github",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "revoke_url": "",  # revocation needs Basic-auth app credentials; see revoke below
        "scopes": "repo read:org",
        "supports_refresh": False,  # classic OAuth-app tokens don't expire
        "extra_authorize_params": {},
        "settings_prefix": "connector_oauth_github",
    },
}


def connector_config(connector_id: str) -> dict[str, Any]:
    cfg = CONNECTOR_PROVIDERS.get(connector_id)
    if cfg is None:
        raise UnknownConnector(f"unknown connector: {connector_id}")
    return cfg


def client_credentials(connector_id: str) -> tuple[str, str]:
    """Resolve (client_id, client_secret) from settings for a connector."""
    from app.core.config import settings

    cfg = connector_config(connector_id)
    prefix = cfg["settings_prefix"]
    client_id = getattr(settings, f"{prefix}_client_id", "") or ""
    client_secret = getattr(settings, f"{prefix}_client_secret", "") or ""
    if not client_id or not client_secret:
        raise ConnectorNotConfigured(
            f"OAuth client credentials for '{connector_id}' are not configured"
        )
    return client_id, client_secret


# ---------------------------------------------------------------------------
# OneShotStore — OAuthStateStore semantics, configurable prefix
# ---------------------------------------------------------------------------


class OneShotStore:
    """TTL'd one-shot ``token → payload dict`` (atomic GETDEL on pop)."""

    def __init__(self, *, redis_client, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._ttl = ttl_seconds

    def put(self, payload: dict, *, token: str | None = None) -> str:
        token = token or uuid.uuid4().hex
        self._redis.set(
            f"{self._prefix}{token}",
            json.dumps(payload, ensure_ascii=False),
            ex=self._ttl,
        )
        return token

    def pop(self, token: str) -> dict | None:
        """Atomic GET+DEL — payload on first call, None after."""
        if not token:
            return None
        key = f"{self._prefix}{token}"
        raw = None
        try:
            raw = self._redis.getdel(key)
        except Exception:
            with self._redis.pipeline() as pipe:
                pipe.get(key)
                pipe.delete(key)
                results = pipe.execute()
                raw = results[0] if results else None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None


TICKET_PREFIX = "connector_ticket:"
STATE_PREFIX = "connector_oauth_state:"
_TTL_SECONDS = 300  # ≤5-minute one-shot for both tickets and state


def mint_ticket_token() -> str:
    """Random 256-bit connect-ticket token (urlsafe)."""
    return secrets.token_urlsafe(32)


_TICKET_STORE: OneShotStore | None = None
_STATE_STORE: OneShotStore | None = None


def _redis_client():
    from app.core.config import settings
    import redis as redis_lib

    return redis_lib.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def get_connector_ticket_store():
    """FastAPI dep — tests override via ``app.dependency_overrides``."""
    global _TICKET_STORE
    if _TICKET_STORE is None:
        _TICKET_STORE = OneShotStore(
            redis_client=_redis_client(),
            prefix=TICKET_PREFIX,
            ttl_seconds=_TTL_SECONDS,
        )
    return _TICKET_STORE


def get_connector_state_store():
    """FastAPI dep — tests override via ``app.dependency_overrides``."""
    global _STATE_STORE
    if _STATE_STORE is None:
        _STATE_STORE = OneShotStore(
            redis_client=_redis_client(),
            prefix=STATE_PREFIX,
            ttl_seconds=_TTL_SECONDS,
        )
    return _STATE_STORE


# ---------------------------------------------------------------------------
# Provider round-trips (tests monkeypatch these whole, like
# exchange_code_for_verified_userinfo in oauth_flow_service)
# ---------------------------------------------------------------------------


def _normalize_token_response(connector_id: str, data: dict) -> dict[str, Any]:
    """Provider token JSON → {access_token, refresh_token, expires_at, scopes}."""
    cfg = connector_config(connector_id)
    if cfg["provider"] == "slack":
        # Slack wraps errors in {ok: false, error: "..."} with HTTP 200.
        if not data.get("ok", False):
            raise TokenExchangeFailed(f"slack oauth error: {data.get('error')}")
    access_token = data.get("access_token")
    if not access_token:
        raise TokenExchangeFailed("provider returned no access_token")
    expires_at = None
    expires_in = data.get("expires_in")
    if expires_in:
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=int(expires_in)
            )
        except (TypeError, ValueError):
            expires_at = None
    return {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token"),
        "expires_at": expires_at,
        "scopes": data.get("scope") or cfg["scopes"],
    }


def exchange_code_for_tokens(
    *,
    connector_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Swap an auth code for provider tokens (PKCE S256).

    Raises ``TokenExchangeFailed`` on any provider-side failure. The
    error text is never surfaced to the browser — the callback route
    maps it to a coarse reason code.
    """
    cfg = connector_config(connector_id)
    client_id, client_secret = client_credentials(connector_id)
    try:
        resp = httpx.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        data = resp.json()
    except Exception as exc:
        raise TokenExchangeFailed(f"code exchange failed: {exc}") from exc
    if resp.status_code != 200 or "error" in data:
        raise TokenExchangeFailed(
            f"provider returned {resp.status_code}: {data.get('error', 'unknown')}"
        )
    return _normalize_token_response(connector_id, data)


def refresh_access_token(
    *, connector_id: str, refresh_token: str
) -> dict[str, Any]:
    """Refresh an expired access token. Raises ``TokenExchangeFailed``."""
    cfg = connector_config(connector_id)
    if not cfg["supports_refresh"]:
        raise TokenExchangeFailed(f"{connector_id} does not support refresh")
    client_id, client_secret = client_credentials(connector_id)
    try:
        resp = httpx.post(
            cfg["token_url"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        data = resp.json()
    except Exception as exc:
        raise TokenExchangeFailed(f"token refresh failed: {exc}") from exc
    if resp.status_code != 200 or "error" in data:
        raise TokenExchangeFailed(
            f"provider returned {resp.status_code}: {data.get('error', 'unknown')}"
        )
    return _normalize_token_response(connector_id, data)


def revoke_token_best_effort(*, connector_id: str, access_token: str) -> None:
    """Tell the provider to revoke, swallowing every failure (disconnect
    must succeed locally even when the provider is unreachable)."""
    try:
        cfg = connector_config(connector_id)
        if cfg["provider"] == "google" and cfg["revoke_url"]:
            httpx.post(
                cfg["revoke_url"], data={"token": access_token}, timeout=10.0
            )
        elif cfg["provider"] == "slack":
            httpx.post(
                cfg["revoke_url"],
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        elif cfg["provider"] == "github":
            client_id, client_secret = client_credentials(connector_id)
            httpx.request(
                "DELETE",
                f"https://api.github.com/applications/{client_id}/token",
                auth=(client_id, client_secret),
                json={"access_token": access_token},
                timeout=10.0,
            )
    except Exception:
        logger.warning(
            "Best-effort token revocation failed for %s", connector_id
        )


# ---------------------------------------------------------------------------
# Encryption helpers (same master-key pattern as provider_key_service)
# ---------------------------------------------------------------------------


def encrypt_token(plaintext: str) -> str:
    from app.core.config import settings
    from app.core.security import encrypt_provider_key

    return encrypt_provider_key(plaintext, settings.effective_master_key)


def decrypt_token(encrypted: str) -> str:
    from app.core.config import settings
    from app.core.security import decrypt_provider_key

    return decrypt_provider_key(encrypted, settings.effective_master_key)
