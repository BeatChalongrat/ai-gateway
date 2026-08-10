"""SSO Phase 1 regression — the Google JWKS parse path must accept the
provider's real response shape.

The AccessDenied incident (2026-08-10): ``verify_google_id_token`` fed
``httpx.get(jwks_url).content`` (bytes) into authlib's
``JsonWebKey.import_key_set``, which only JSON-parses a *str* that
``endswith('}')`` — bytes always raise ``ValueError('Invalid key set
format')``, and even ``.text`` fails because Google's pretty-printed
body ends ``'\\n}\\n'``. Every unit test mocked the verifier away, so
nothing exercised the parse. These tests drive the REAL parse + verify
code with a Google-shaped body (only the HTTP call is stubbed), plus
guards that pin the authlib behaviours the fix relies on.
"""

from __future__ import annotations

import json
import time

import pytest

from authlib.jose import JsonWebKey, jwt as authlib_jwt

from app.core.config import settings
from app.services.portal import oauth_flow_service
from app.services.portal.oauth_flow_service import (
    IdTokenVerificationFailed,
    verify_google_id_token,
)


TEST_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture(scope="module")
def rsa_key():
    return JsonWebKey.generate_key("RSA", 2048, is_private=True)


@pytest.fixture(scope="module")
def public_jwk(rsa_key):
    jwk = rsa_key.as_dict(is_private=False)
    jwk["kid"] = "test-kid-1"
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _google_style_body(public_jwk: dict) -> str:
    """Serialize a JWKS exactly the way Google's endpoint does:
    pretty-printed JSON with a trailing newline — the body shape that
    breaks authlib's ``endswith('}')`` heuristic."""
    return json.dumps({"keys": [public_jwk]}, indent=2) + "\n"


class _FakeJwksResponse:
    def __init__(self, body_text: str):
        self._text = body_text

    @property
    def text(self) -> str:
        return self._text

    @property
    def content(self) -> bytes:
        return self._text.encode()

    def json(self):
        return json.loads(self._text)


def _mint_id_token(rsa_key, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": TEST_CLIENT_ID,
        "sub": "google-sub-1",
        "email": "user@gbtech.co.th",
        "email_verified": True,
        "hd": "gbtech.co.th",
        "name": "Test User",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(claim_overrides)
    header = {"alg": "RS256", "kid": "test-kid-1"}
    return authlib_jwt.encode(header, claims, rsa_key).decode()


@pytest.fixture
def google_configured(monkeypatch, public_jwk):
    monkeypatch.setattr(settings, "oauth_google_client_id", TEST_CLIENT_ID)

    body = _google_style_body(public_jwk)

    def _fake_get(url, timeout=None):
        assert "googleapis.com" in url
        return _FakeJwksResponse(body)

    # verify_google_id_token imports httpx inside the function; patch
    # the module-level attribute it resolves at call time.
    import httpx

    monkeypatch.setattr(httpx, "get", _fake_get)


class TestVerifyGoogleIdTokenParsesRealJwksShape:
    def test_google_pretty_printed_jwks_verifies_end_to_end(
        self, google_configured, rsa_key
    ):
        """The real parse path: Google-shaped body (pretty-printed +
        trailing newline) must get through import_key_set AND verify a
        properly signed token — the incident's failure mode returned
        'Invalid key set format' before signature checking began."""
        claims = verify_google_id_token(_mint_id_token(rsa_key))
        assert claims["email"] == "user@gbtech.co.th"
        assert claims["email_verified"] is True
        assert claims["hd"] == "gbtech.co.th"
        assert claims["sub"] == "google-sub-1"

    def test_bad_signature_fails_after_keyset_not_at_keyset(
        self, google_configured, rsa_key
    ):
        """A token signed by a DIFFERENT key must fail at signature
        verification — proving the key set itself was imported fine."""
        other_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        forged = _mint_id_token(other_key)
        with pytest.raises(IdTokenVerificationFailed) as exc:
            verify_google_id_token(forged)
        assert "Invalid key set format" not in str(exc.value)

    def test_wrong_audience_fails_after_keyset(self, google_configured, rsa_key):
        with pytest.raises(IdTokenVerificationFailed) as exc:
            verify_google_id_token(
                _mint_id_token(rsa_key, aud="someone-else-client-id")
            )
        assert "Invalid key set format" not in str(exc.value)


class TestAuthlibKeySetContract:
    """Pin the authlib behaviours the .json() fix relies on. If an
    authlib upgrade changes them, these fail loudly and the JWKS call
    sites (oauth_flow_service ~L222 / ~L401) must be re-audited."""

    def test_bytes_body_is_rejected(self, public_jwk):
        with pytest.raises(ValueError, match="Invalid key set format"):
            JsonWebKey.import_key_set(_google_style_body(public_jwk).encode())

    def test_pretty_text_with_trailing_newline_is_rejected(self, public_jwk):
        with pytest.raises(ValueError, match="Invalid key set format"):
            JsonWebKey.import_key_set(_google_style_body(public_jwk))

    def test_dict_is_accepted(self, public_jwk):
        key_set = JsonWebKey.import_key_set(
            json.loads(_google_style_body(public_jwk))
        )
        assert len(key_set.keys) == 1


class TestPkceExchangePath:
    """A2 of this fix: the desktop PKCE path
    (exchange_code_for_verified_userinfo) had the identical bug — its
    JWKS fetch also fed bytes into import_key_set. Stub the OAuth2Client
    round-trip and drive the real parse + verify code."""

    def test_google_pkce_exchange_verifies_with_real_jwks_shape(
        self, monkeypatch, rsa_key, public_jwk
    ):
        monkeypatch.setattr(settings, "oauth_google_client_id", TEST_CLIENT_ID)
        monkeypatch.setattr(
            settings, "oauth_google_client_secret", "test-secret"
        )
        body = _google_style_body(public_jwk)
        id_token = _mint_id_token(rsa_key)

        class _FakeOAuth2Client:
            def __init__(self, **kwargs):
                pass

            def fetch_token(self, url, **kwargs):
                return {"id_token": id_token, "access_token": "at-1"}

            def get(self, url, **kwargs):
                return _FakeJwksResponse(body)

        import authlib.integrations.httpx_client as _authlib_httpx

        monkeypatch.setattr(_authlib_httpx, "OAuth2Client", _FakeOAuth2Client)

        claims = oauth_flow_service.exchange_code_for_verified_userinfo(
            provider="google",
            code="auth-code",
            code_verifier="v" * 64,
            redirect_uri="http://127.0.0.1:8765/callback",
        )
        assert claims["email"] == "user@gbtech.co.th"
        assert claims["email_verified"] is True
        assert claims["sub"] == "google-sub-1"
