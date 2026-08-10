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
            json.dumps(payload, ensure_ascii=False),
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

            # MUST be .json() (a dict): authlib's import_key_set only
            # JSON-parses a *str* that endswith('}') — bytes always
            # fail, and Google's pretty-printed body ends '\n}\n' so
            # even .text fails ("Invalid key set format").
            jwks_data = client.get(provider_cfg["jwks_url"]).json()
            jwks = JsonWebKey.import_key_set(jwks_data)
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
            # Surfaced for the domain-lock gate in login_or_create.
            # ``hd`` is informational only — enforcement is email domain
            # + email_verified.
            "email_verified": bool(claims.get("email_verified")),
            "hd": claims.get("hd"),
        }

    # GitHub — no id_token; use userinfo endpoint and email endpoint.
    try:
        user = client.get(provider_cfg["userinfo_url"]).json()
        emails: list = []
        try:
            emails = client.get(provider_cfg["emails_url"]).json() or []
        except Exception:
            # No email scope / endpoint failure → we cannot prove
            # verification. Fail closed (email_verified=False) below.
            emails = []
        email, email_is_verified = _github_pick_verified_email(
            user.get("email"), emails
        )
        if not email:
            raise IdTokenVerificationFailed("GitHub returned no verified email")
        return {
            "email": email,
            "sub": str(user.get("id")),
            "name": user.get("name") or user.get("login"),
            "picture": user.get("avatar_url"),
            "email_verified": email_is_verified,
            "hd": None,
        }
    except IdTokenVerificationFailed:
        raise
    except Exception as exc:
        raise IdTokenVerificationFailed(f"userinfo fetch failed: {exc}") from exc


def _github_pick_verified_email(
    public_email: str | None, emails: list
) -> tuple[str | None, bool]:
    """Resolve (email, verified?) from GitHub's /user + /user/emails data.

    GitHub has no OIDC ``email_verified`` claim, so verification status
    must come from the /user/emails list. A public /user email that we
    can't find in that list counts as UNVERIFIED (fail closed) — the
    domain-lock gate downstream will reject it.
    """
    if public_email:
        match = next(
            (
                e
                for e in emails
                if (e.get("email") or "").lower() == public_email.lower()
            ),
            None,
        )
        return public_email, bool(match and match.get("verified"))
    primary = next(
        (e for e in emails if e.get("primary") and e.get("verified")),
        None,
    )
    if primary:
        return primary["email"], True
    return None, False


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


# ---------------------------------------------------------------------------
# Token-only verifiers
# ---------------------------------------------------------------------------
#
# Used by ``POST /portal/auth/oauth/verify`` (Session 5 — NextAuth
# integration). NextAuth performs the OAuth dance with the provider
# itself, so the backend never sees a code; instead the portal forwards
# the provider-issued id_token (Google) or the access_token (GitHub)
# and the backend verifies it server-side.
#
# Security note: NEVER trust caller-supplied claims like ``sub`` or
# ``email`` directly. Both functions below derive the claims from the
# token itself — for Google by validating the JWT signature against the
# JWKS endpoint, for GitHub by calling the provider's userinfo endpoint
# with the access token. Anything outside the verified-claims path is
# treated as forged.


def verify_google_id_token(id_token: str) -> dict[str, Any]:
    """Verify a Google OpenID Connect id_token and return safe claims.

    Raises ``IdTokenVerificationFailed`` if the signature, issuer, or
    audience don't check out. The caller MUST treat this as "no other
    fields are trustable" — that's the entire point of the verifier.
    """
    if not id_token or not isinstance(id_token, str):
        raise IdTokenVerificationFailed("id_token is required")
    try:
        from authlib.jose import JsonWebKey, JsonWebToken
        from authlib.oidc.core import CodeIDToken
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise IdTokenVerificationFailed(
            "authlib + httpx are required for OAuth verification"
        ) from exc

    cfg = _provider_config("google")
    if not cfg["client_id"]:
        raise IdTokenVerificationFailed("Google OAuth not configured")
    try:
        # JWKS round-trip is a fresh HTTP call each verify. Acceptable
        # for the login latency budget (~150ms); a future improvement
        # is an in-process cache keyed by ``kid`` with a 1-hour TTL.
        # MUST be .json() (a dict): authlib's import_key_set only
        # JSON-parses a *str* that endswith('}') — bytes always fail,
        # and Google's pretty-printed body ends '\n}\n' so even .text
        # fails ("Invalid key set format").
        jwks_data = httpx.get(cfg["jwks_url"], timeout=10.0).json()
        jwks = JsonWebKey.import_key_set(jwks_data)
        claims = JsonWebToken(["RS256"]).decode(
            id_token,
            jwks,
            claims_cls=CodeIDToken,
            claims_options={
                "iss": {"values": cfg["issuer"]},
                "aud": {"value": cfg["client_id"]},
            },
        )
        claims.validate()
    except Exception as exc:
        raise IdTokenVerificationFailed(
            f"Google id_token verification failed: {exc}"
        ) from exc
    return {
        "email": claims.get("email"),
        "sub": claims.get("sub"),
        "name": claims.get("name") or claims.get("given_name"),
        "picture": claims.get("picture"),
        # Surfaced for the domain-lock gate in login_or_create. ``hd``
        # is informational only — enforcement is email + email_verified.
        "email_verified": bool(claims.get("email_verified")),
        "hd": claims.get("hd"),
    }


def verify_github_access_token(access_token: str) -> dict[str, Any]:
    """Verify a GitHub access_token by fetching userinfo.

    GitHub doesn't issue id_tokens (no OIDC) so the verification path
    is "ask the provider who this token belongs to". A forged token
    fails the call. Email may be private on /user; we fall back to
    /user/emails and take the primary verified address.
    """
    if not access_token or not isinstance(access_token, str):
        raise IdTokenVerificationFailed("access_token is required")
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise IdTokenVerificationFailed("httpx is required for OAuth verification") from exc

    cfg = _provider_config("github")
    if not cfg["client_id"]:
        raise IdTokenVerificationFailed("GitHub OAuth not configured")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "sran-portal-oauth",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(cfg["userinfo_url"], headers=headers)
            if resp.status_code != 200:
                raise IdTokenVerificationFailed(
                    f"GitHub /user returned {resp.status_code}"
                )
            user = resp.json()
            emails: list = []
            emails_resp = client.get(cfg["emails_url"], headers=headers)
            if emails_resp.status_code == 200:
                emails = emails_resp.json() or []
            email, email_is_verified = _github_pick_verified_email(
                user.get("email"), emails
            )
            if not email:
                raise IdTokenVerificationFailed("GitHub returned no verified email")
    except IdTokenVerificationFailed:
        raise
    except Exception as exc:
        raise IdTokenVerificationFailed(
            f"GitHub userinfo fetch failed: {exc}"
        ) from exc
    return {
        "email": email,
        "sub": str(user.get("id")),
        "name": user.get("name") or user.get("login"),
        "picture": user.get("avatar_url"),
        "email_verified": email_is_verified,
        "hd": None,
    }
