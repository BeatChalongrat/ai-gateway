"""Phase 2A — Commit 4: server-side OAuth (P0-8 fix) (TDD).

Replaces the previous portal OAuth flow, which trusted a caller-supplied
``{oauth_id, email, display_name}`` body, with a server-driven flow:

1. ``GET /portal/auth/oauth/start?provider=google&return_to=...``
   mints a random ``state`` and PKCE ``code_verifier`` + challenge,
   stores them in Redis under a 5-minute one-shot key, and redirects
   the browser to the provider's authorize URL.

2. ``GET /portal/auth/oauth/callback?code=...&state=...`` pops the
   state row from Redis (atomic GETDEL — one-shot), exchanges the
   code for an ID token using the stored PKCE verifier, verifies
   the id_token via the provider's JWKS, and calls
   ``oauth_service.login_or_create`` with VERIFIED fields.

3. The previous ``POST /portal/auth/oauth/callback`` accepts a JSON
   body — that endpoint is replaced by a ``410 Gone`` handler that
   points at the new GET flow.

Replays of the old P0-8 exploit paths:
* Forge state → unknown state → 400.
* Reuse state → first call succeeds, second call gets 400 (one-shot).
* Omit state → 400.
* return_to outside the whitelist → 400.
* id_token with a wrong signature → authlib raises, callback → 400.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.services.portal.oauth_flow_service import (
    InvalidReturnUrl,
    InvalidState,
    OAuthStateStore,
)


# ---------------------------------------------------------------------------
# OAuthStateStore — Redis-backed one-shot store with TTL
# ---------------------------------------------------------------------------


class TestOAuthStateStore:
    def test_put_then_get_once_returns_value(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OAuthStateStore(redis_client=redis, ttl_seconds=60)
        state = store.put(
            provider="google",
            code_verifier="V" * 64,
            return_to="https://portal.example/app",
        )
        row = store.pop(state)
        assert row is not None
        assert row["provider"] == "google"
        assert row["code_verifier"] == "V" * 64
        assert row["return_to"] == "https://portal.example/app"

    def test_pop_is_one_shot(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OAuthStateStore(redis_client=redis, ttl_seconds=60)
        state = store.put(provider="google", code_verifier="v", return_to="/")
        assert store.pop(state) is not None
        # Second pop must fail — the row was deleted on the first.
        assert store.pop(state) is None

    def test_pop_unknown_state_returns_none(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OAuthStateStore(redis_client=redis, ttl_seconds=60)
        assert store.pop("nope") is None

    def test_state_expires_after_ttl(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OAuthStateStore(redis_client=redis, ttl_seconds=1)
        state = store.put(provider="google", code_verifier="v", return_to="/")
        time.sleep(1.1)
        assert store.pop(state) is None

    def test_state_uuid_is_unguessable(self):
        """Sanity: two consecutive puts produce different state ids."""
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OAuthStateStore(redis_client=redis, ttl_seconds=60)
        a = store.put(provider="google", code_verifier="v", return_to="/")
        b = store.put(provider="google", code_verifier="v", return_to="/")
        assert a != b


# ---------------------------------------------------------------------------
# return_to whitelist
# ---------------------------------------------------------------------------


class TestReturnUrlWhitelist:
    def test_relative_same_origin_accepted(self):
        from app.services.portal.oauth_flow_service import validate_return_to
        validate_return_to("/app/dashboard", allowed_origins=["https://portal.example"])

    def test_absolute_whitelisted_origin_accepted(self):
        from app.services.portal.oauth_flow_service import validate_return_to
        validate_return_to(
            "https://portal.example/app",
            allowed_origins=["https://portal.example"],
        )

    def test_unknown_origin_rejected(self):
        from app.services.portal.oauth_flow_service import validate_return_to
        with pytest.raises(InvalidReturnUrl):
            validate_return_to(
                "https://evil.example/steal",
                allowed_origins=["https://portal.example"],
            )

    def test_javascript_scheme_rejected(self):
        from app.services.portal.oauth_flow_service import validate_return_to
        with pytest.raises(InvalidReturnUrl):
            validate_return_to(
                "javascript:alert(1)",
                allowed_origins=["https://portal.example"],
            )

    def test_protocol_relative_rejected(self):
        """``//evil.example`` would be interpreted as an absolute URL
        by most browsers — block the redirect target outright.
        """
        from app.services.portal.oauth_flow_service import validate_return_to
        with pytest.raises(InvalidReturnUrl):
            validate_return_to(
                "//evil.example/steal",
                allowed_origins=["https://portal.example"],
            )


# ---------------------------------------------------------------------------
# Endpoint-level — mocks authlib for the actual code exchange
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def state_store(redis_client):
    return OAuthStateStore(redis_client=redis_client, ttl_seconds=300)


@pytest.fixture
def oauth_client(state_store):
    """Returns a TestClient for the portal_auth router with mocked deps."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api.deps import get_db_session
    from app.api.routes.portal_auth import router as portal_auth_router
    from app.core.db import Base
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )
    from app.services.portal.oauth_flow_service import (
        get_oauth_state_store,
    )
    import app.models  # noqa: F401

    app = FastAPI()
    app.add_exception_handler(GatewayError, handle_gateway_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_oauth_state_store] = lambda: state_store
    app.include_router(portal_auth_router, prefix="/portal/auth")
    return TestClient(app), state_store


class TestOAuthCallback:
    def test_callback_without_state_returns_400(self, oauth_client):
        client, _store = oauth_client
        r = client.get("/portal/auth/oauth/callback?code=xyz")
        assert r.status_code in (400, 422)

    def test_callback_with_unknown_state_returns_400(self, oauth_client):
        client, _store = oauth_client
        r = client.get(
            "/portal/auth/oauth/callback?code=xyz&state=never-issued"
        )
        assert r.status_code in (400, 422)
        body = r.json()
        assert body["error"]["code"] in {"invalid_state", "validation_error"}

    def test_state_is_consumed_on_first_use(
        self, oauth_client, monkeypatch
    ):
        """After a successful callback, the state row must be gone —
        a replay attempt with the same state parameter must fail.
        """
        client, store = oauth_client

        # Patch authlib's code-for-token exchange to a stub that returns
        # a verified userinfo payload.
        from app.services.portal import oauth_flow_service

        def _fake_exchange(**kwargs):
            # Phase 0: claims must carry a verified email on an allowed
            # domain or login_or_create rejects them before minting.
            return {
                "email": "newuser@gbtech.co.th",
                "sub": "provider-sub-123",
                "name": "New User",
                "picture": None,
                "email_verified": True,
                "hd": "gbtech.co.th",
            }

        monkeypatch.setattr(
            oauth_flow_service, "exchange_code_for_verified_userinfo",
            _fake_exchange,
        )

        state = store.put(
            provider="google",
            code_verifier="v" * 64,
            return_to="/app",
        )
        # First callback — normal flow.
        r1 = client.get(
            f"/portal/auth/oauth/callback?code=abc&state={state}",
            follow_redirects=False,
        )
        # Happy path should either 2xx (JSON tokens) or 302-redirect
        # with tokens on a fragment. The critical bit is "state was
        # consumed".
        assert r1.status_code < 400, r1.text

        # Replay — same state.
        r2 = client.get(
            f"/portal/auth/oauth/callback?code=abc&state={state}"
        )
        assert r2.status_code in (400, 422)
        assert r2.json()["error"]["code"] == "invalid_state"

    def test_id_token_verification_failure_returns_400(
        self, oauth_client, monkeypatch
    ):
        client, store = oauth_client
        from app.services.portal import oauth_flow_service

        def _raise(**kwargs):
            raise oauth_flow_service.IdTokenVerificationFailed(
                "bad signature"
            )

        monkeypatch.setattr(
            oauth_flow_service, "exchange_code_for_verified_userinfo",
            _raise,
        )

        state = store.put(
            provider="google", code_verifier="v", return_to="/",
        )
        r = client.get(
            f"/portal/auth/oauth/callback?code=abc&state={state}"
        )
        assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Legacy POST callback → 410
# ---------------------------------------------------------------------------


class TestLegacyCallbackGone:
    def test_post_callback_returns_410(self, oauth_client):
        client, _ = oauth_client
        r = client.post(
            "/portal/auth/oauth/callback",
            json={
                "provider": "google",
                "oauth_id": "1234",
                "email": "attacker@example.com",
                "display_name": "Mallory",
            },
        )
        assert r.status_code == 410

    def test_410_body_points_at_new_endpoint(self, oauth_client):
        client, _ = oauth_client
        r = client.post(
            "/portal/auth/oauth/callback",
            json={"provider": "google", "oauth_id": "x", "email": "x@x.com"},
        )
        body = r.json()
        assert "/portal/auth/oauth/start" in str(body)
