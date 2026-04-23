"""Server-side OAuth flow for the portal (Phase 2A, Commit 4).

Replaces the previous ``POST /portal/auth/oauth/callback`` — which
trusted a caller-supplied identity body — with a proper server-driven
flow: the FastAPI backend holds the client secret, mints the state +
PKCE verifier, exchanges the code, verifies the id_token, and ONLY
THEN calls ``oauth_service.login_or_create`` with trusted fields.

The module exposes three pieces:

* ``OAuthStateStore`` — Redis-backed, TTL'd, one-shot store of
  ``state → {provider, code_verifier, return_to}``. ``pop`` is atomic
  GET+DEL; a second ``pop`` of the same state returns ``None``. This
  blocks state replay and state-brute-force attacks simultaneously.

* ``validate_return_to`` — safelist check for the post-auth redirect
  target. Rejects javascript:, protocol-relative (``//evil``), and
  absolute URLs whose origin isn't on the allow-list.

* ``exchange_code_for_verified_userinfo`` — wraps authlib's authorize-
  flow + id_token verification. Raises ``IdTokenVerificationFailed``
  on any step that cannot be cryptographically trusted.

Redis keys used:
  ``oauth_state:<uuid>`` — JSON blob, ``EX=ttl``; GETDEL on consume.
"""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Any
from urllib.parse import urlsplit


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidState(Exception):
    """state is unknown, expired, or was already consumed."""


class InvalidReturnUrl(Exception):
    """return_to parameter is not on the safelist."""


class IdTokenVerificationFailed(Exception):
    """authlib rejected the provider's id_token."""


# ---------------------------------------------------------------------------
# OAuthStateStore
# ---------------------------------------------------------------------------


_KEY_PREFIX = "oauth_state:"


class OAuthStateStore:
    """TTL'd one-shot ``state → {provider, code_verifier, return_to}``."""

    def __init__(self, *, redis_client, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    def put(
        self,
        *,
        provider: str,
        code_verifier: str,
        return_to: str,
    ) -> str:
        state = uuid.uuid4().hex
        payload = {
            "provider": provider,
            "code_verifier": code_verifier,
            "return_to": return_to,
        }
        self._redis.set(
            f"{_KEY_PREFIX}{state}",
            json.dumps(payload),
            ex=self._ttl,
        )
        return state

    def pop(self, state: str) -> dict | None:
        """Atomic GET+DEL — returns the payload on first call, None after."""
        if not state:
            return None
        key = f"{_KEY_PREFIX}{state}"
        # Redis 6.2+ has GETDEL natively; fakeredis supports it. Fall
        # back to a pipeline for older servers just in case.
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


def get_oauth_state_store():
    """FastAPI dep: lazy singleton wired to the real Redis client.

    Tests override via ``app.dependency_overrides``.
    """
    from app.core.config import settings
    import redis as redis_lib

    global _STATE_STORE
    if _STATE_STORE is None:
        _STATE_STORE = OAuthStateStore(
            redis_client=redis_lib.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            ),
            ttl_seconds=300,  # 5-minute one-shot
        )
    return _STATE_STORE


_STATE_STORE: OAuthStateStore | None = None


# ---------------------------------------------------------------------------
# return_to safelist
# ---------------------------------------------------------------------------


def validate_return_to(return_to: str, *, allowed_origins: list[str]) -> str:
    """Reject non-safelist redirect targets. Returns the validated URL."""
    if not return_to:
        raise InvalidReturnUrl("empty return_to")
    if return_to.startswith("//"):
        raise InvalidReturnUrl("protocol-relative URL is not allowed")
    if ":" in return_to[:20].lower():
        # Any scheme prefix must be http/https on an allowed origin.
        scheme = return_to.split(":", 1)[0].lower()
        if scheme not in {"http", "https"}:
            raise InvalidReturnUrl(f"disallowed scheme: {scheme}")
        parts = urlsplit(return_to)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in allowed_origins:
            raise InvalidReturnUrl(f"origin not allowed: {origin}")
        return return_to
    # Relative path — accept as same-origin.
    if not return_to.startswith("/"):
        raise InvalidReturnUrl("must be absolute or start with /")
    return return_to


# ---------------------------------------------------------------------------
# Code exchange + id_token verification (authlib)
# ---------------------------------------------------------------------------


def exchange_code_for_verified_userinfo(
    *,
    provider: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Swap an auth code for verified userinfo, raise on any failure.

    Wrapper around authlib. The caller must have already validated
    the state token — this function never sees ``state`` because its
    only job is cryptographic: swap code+verifier for tokens, verify
    id_token signature against the provider's JWKS, and return only
    the claims we trust.

    Tests monkeypatch this whole function with a canned response; the
    real implementation is kept intentionally thin so there's not much
    logic to unit-test beyond a provider round-trip.
    """
    try:
        from authlib.integrations.httpx_client import OAuth2Client
    except ImportError as exc:  # pragma: no cover
        raise IdTokenVerificationFailed(
            "authlib is required for OAuth — install authlib"
        ) from exc

    provider_cfg = _provider_config(provider)
    client = OAuth2Client(
        client_id=provider_cfg["client_id"],
        client_secret=provider_cfg["client_secret"],
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    )
    try:
        token = client.fetch_token(
            provider_cfg["token_url"],
            code=code,
            code_verifier=code_verifier,
            grant_type="authorization_code",
        )
    except Exception as exc:
        raise IdTokenVerificationFailed(f"code exchange failed: {exc}") from exc

    # Extract+verify userinfo. For Google we prefer id_token claims
    # (already signed, no extra RTT); for GitHub we hit /user since
    # GitHub doesn't issue an id_token.
    if provider == "google":
        try:
            from authlib.jose import JsonWebToken, JsonWebKey  # noqa: F401
            from authlib.oidc.core import CodeIDToken

            jwks_bytes = client.get(provider_cfg["jwks_url"]).content
            jwks = JsonWebKey.import_key_set(jwks_bytes)
            claims = JsonWebToken(["RS256"]).decode(
                token["id_token"],
                jwks,
                claims_cls=CodeIDToken,
                claims_options={
                    "iss": {"values": provider_cfg["issuer"]},
                    "aud": {"value": provider_cfg["client_id"]},
                },
            )
            claims.validate()
        except Exception as exc:
            raise IdTokenVerificationFailed(
                f"id_token verification failed: {exc}"
            ) from exc

        return {
            "email": claims.get("email"),
            "sub": claims.get("sub"),
            "name": claims.get("name") or claims.get("given_name"),
            "picture": claims.get("picture"),
        }

    # GitHub — no id_token; use userinfo endpoint and email endpoint.
    try:
        user = client.get(provider_cfg["userinfo_url"]).json()
        email = user.get("email")
        if not email:
            # GitHub may omit email from /user if it's private; fall
            # back to /user/emails.
            emails = client.get(provider_cfg["emails_url"]).json()
            primary = next(
                (e["email"] for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            email = primary
        if not email:
            raise IdTokenVerificationFailed("GitHub returned no verified email")
        return {
            "email": email,
            "sub": str(user.get("id")),
            "name": user.get("name") or user.get("login"),
            "picture": user.get("avatar_url"),
        }
    except IdTokenVerificationFailed:
        raise
    except Exception as exc:
        raise IdTokenVerificationFailed(f"userinfo fetch failed: {exc}") from exc


def _provider_config(provider: str) -> dict:
    from app.core.config import settings

    if provider == "google":
        return {
            "client_id": getattr(settings, "oauth_google_client_id", "") or "",
            "client_secret": getattr(settings, "oauth_google_client_secret", "") or "",
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
            "issuer": ("https://accounts.google.com", "accounts.google.com"),
            "userinfo_url": "",
            "emails_url": "",
            "scope": "openid email profile",
        }
    if provider == "github":
        return {
            "client_id": getattr(settings, "oauth_github_client_id", "") or "",
            "client_secret": getattr(settings, "oauth_github_client_secret", "") or "",
            "authorize_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "jwks_url": "",
            "issuer": (),
            "userinfo_url": "https://api.github.com/user",
            "emails_url": "https://api.github.com/user/emails",
            "scope": "read:user user:email",
        }
    raise IdTokenVerificationFailed(f"unsupported provider: {provider}")


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    import base64
    import hashlib

    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge
