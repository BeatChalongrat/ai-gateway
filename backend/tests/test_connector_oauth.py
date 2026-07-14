"""Connector OAuth broker (Desktop Phase 7, PART A) — TDD suite.

Covers the Ground-Rules invariants:

* Connect tickets are single-use (atomic GETDEL) with a 5-min TTL.
* OAuth state is one-shot — a replayed callback fails closed.
* The callback stores tokens ENCRYPTED (ciphertext != plaintext,
  decrypts back) and the deep-link redirect never carries a token.
* ``POST /{id}/token`` refreshes an expired token first and NEVER
  returns the refresh token.
* Cross-user access is a 404 (connection rows are per-user).
* Disconnect deletes the row (+ best-effort provider revocation).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.services.portal.connector_oauth_service import (
    OneShotStore,
    STATE_PREFIX,
    TICKET_PREFIX,
)


# ---------------------------------------------------------------------------
# OneShotStore — OAuthStateStore semantics, connector prefixes
# ---------------------------------------------------------------------------


class TestOneShotStore:
    def test_pop_is_one_shot(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OneShotStore(
            redis_client=redis, prefix=TICKET_PREFIX, ttl_seconds=60
        )
        token = store.put({"user_id": "u1", "connector_id": "github"})
        assert store.pop(token) == {"user_id": "u1", "connector_id": "github"}
        assert store.pop(token) is None

    def test_prefixes_are_isolated(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        tickets = OneShotStore(
            redis_client=redis, prefix=TICKET_PREFIX, ttl_seconds=60
        )
        states = OneShotStore(
            redis_client=redis, prefix=STATE_PREFIX, ttl_seconds=60
        )
        token = tickets.put({"x": 1}, token="shared")
        assert states.pop(token) is None  # different key space
        assert tickets.pop(token) == {"x": 1}

    def test_unknown_token_returns_none(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        store = OneShotStore(
            redis_client=redis, prefix=TICKET_PREFIX, ttl_seconds=60
        )
        assert store.pop("never-issued") is None


# ---------------------------------------------------------------------------
# Route-level fixture: standalone app, sqlite, fakeredis stores
# ---------------------------------------------------------------------------


@pytest.fixture
def broker_env():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api.deps import get_db_session, get_portal_user
    from app.api.routes.portal_connectors import router as connectors_router
    from app.core.db import Base
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )
    from app.models.portal_user import PortalUserORM
    from app.services.portal.connector_oauth_service import (
        get_connector_state_store,
        get_connector_ticket_store,
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

    redis = fakeredis.FakeRedis(decode_responses=True)
    ticket_store = OneShotStore(
        redis_client=redis, prefix=TICKET_PREFIX, ttl_seconds=300
    )
    state_store = OneShotStore(
        redis_client=redis, prefix=STATE_PREFIX, ttl_seconds=300
    )

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_connector_ticket_store] = lambda: ticket_store
    app.dependency_overrides[get_connector_state_store] = lambda: state_store
    app.include_router(connectors_router, prefix="/portal/connectors")

    s = Session()
    user_a = PortalUserORM(
        id=str(uuid.uuid4()), email="a@example.com", password_hash="x"
    )
    user_b = PortalUserORM(
        id=str(uuid.uuid4()), email="b@example.com", password_hash="x"
    )
    s.add_all([user_a, user_b])
    s.commit()
    a_id, b_id = user_a.id, user_b.id
    s.close()

    def _login_as(user_id: str):
        sess = Session()
        user = sess.get(PortalUserORM, user_id)
        sess.close()
        app.dependency_overrides[get_portal_user] = lambda: user
        return user

    return {
        "client": TestClient(app),
        "Session": Session,
        "user_a": a_id,
        "user_b": b_id,
        "login_as": _login_as,
        "ticket_store": ticket_store,
        "state_store": state_store,
    }


def _seed_row(
    env,
    *,
    user_id: str,
    connector_id: str = "github",
    access_token: str = "tok_access_plain",
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
):
    from app.models.connector_token import ConnectorTokenORM
    from app.services.portal.connector_oauth_service import encrypt_token

    s = env["Session"]()
    row = ConnectorTokenORM(
        id=str(uuid.uuid4()),
        portal_user_id=user_id,
        connector_id=connector_id,
        provider="github",
        access_token_encrypted=encrypt_token(access_token),
        refresh_token_encrypted=(
            encrypt_token(refresh_token) if refresh_token else None
        ),
        expires_at=expires_at,
        scopes="repo",
    )
    s.add(row)
    s.commit()
    s.close()


# ---------------------------------------------------------------------------
# Ticket → start
# ---------------------------------------------------------------------------


class TestConnectTicket:
    def test_ticket_is_single_use(self, broker_env, monkeypatch):
        from app.services.portal import connector_oauth_service

        monkeypatch.setattr(
            connector_oauth_service,
            "client_credentials",
            lambda connector_id: ("cid", "csecret"),
        )
        # The route module imported client_credentials into its own
        # namespace only for start's direct call path via the service
        # module — patch both to be safe.
        import app.api.routes.portal_connectors as routes

        monkeypatch.setattr(
            routes.connector_oauth_service,
            "client_credentials",
            lambda connector_id: ("cid", "csecret"),
        )

        env = broker_env
        env["login_as"](env["user_a"])
        r = env["client"].post("/portal/connectors/github/ticket")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticket"]
        assert "oauth/start" in body["start_url"]
        assert body["ticket"] in body["start_url"]

        r1 = env["client"].get(
            f"/portal/connectors/oauth/start?ticket={body['ticket']}",
            follow_redirects=False,
        )
        assert r1.status_code == 302
        loc = r1.headers["location"]
        assert loc.startswith("https://github.com/login/oauth/authorize")
        assert "code_challenge_method=S256" in loc
        # No portal JWT / token material anywhere in the authorize URL.
        assert "tok_" not in loc

        # Replay: the ticket was consumed by the first start.
        r2 = env["client"].get(
            f"/portal/connectors/oauth/start?ticket={body['ticket']}"
        )
        assert r2.status_code in (400, 422)
        assert r2.json()["error"]["code"] == "invalid_ticket"

    def test_unknown_connector_is_404(self, broker_env):
        env = broker_env
        env["login_as"](env["user_a"])
        r = env["client"].post("/portal/connectors/doesnotexist/ticket")
        assert r.status_code == 404

    def test_ticket_requires_auth(self, broker_env):
        env = broker_env
        # No get_portal_user override → the real dependency runs and 401s.
        from app.api.deps import get_portal_user

        env["client"].app.dependency_overrides.pop(get_portal_user, None)
        r = env["client"].post("/portal/connectors/github/ticket")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Callback: one-shot state, encrypted storage, token-free deep link
# ---------------------------------------------------------------------------


class TestCallback:
    def _put_state(self, env, user_id: str, connector_id: str = "github"):
        return env["state_store"].put(
            {
                "user_id": user_id,
                "tenant_id": None,
                "connector_id": connector_id,
                "code_verifier": "v" * 64,
            }
        )

    def test_callback_stores_encrypted_and_state_is_one_shot(
        self, broker_env, monkeypatch
    ):
        from app.models.connector_token import ConnectorTokenORM
        from app.services.portal.connector_oauth_service import decrypt_token
        import app.api.routes.portal_connectors as routes

        plaintext = "tok_provider_access_SECRET"

        def _fake_exchange(**kwargs):
            return {
                "access_token": plaintext,
                "refresh_token": "tok_provider_refresh_SECRET",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                "scopes": "repo",
            }

        monkeypatch.setattr(
            routes.connector_oauth_service,
            "exchange_code_for_tokens",
            _fake_exchange,
        )

        env = broker_env
        state = self._put_state(env, env["user_a"])
        r = env["client"].get(
            f"/portal/connectors/oauth/callback?code=abc&state={state}"
        )
        assert r.status_code == 200
        assert "sran://oauth/callback?connector=github&status=ok" in r.text
        # The deep-link page NEVER carries token material.
        assert "tok_provider" not in r.text
        assert "SECRET" not in r.text

        s = env["Session"]()
        row = s.query(ConnectorTokenORM).filter_by(
            portal_user_id=env["user_a"], connector_id="github"
        ).one()
        # Encrypted at rest: ciphertext != plaintext, decrypts back.
        assert row.access_token_encrypted != plaintext
        assert plaintext not in row.access_token_encrypted
        assert decrypt_token(row.access_token_encrypted) == plaintext
        assert (
            decrypt_token(row.refresh_token_encrypted)
            == "tok_provider_refresh_SECRET"
        )
        s.close()

        # Replay the same state — must fail closed.
        r2 = env["client"].get(
            f"/portal/connectors/oauth/callback?code=abc&state={state}"
        )
        assert "status=error" in r2.text
        assert "reason=invalid_state" in r2.text

    def test_provider_denied_maps_to_coarse_reason(self, broker_env):
        env = broker_env
        state = self._put_state(env, env["user_a"])
        r = env["client"].get(
            "/portal/connectors/oauth/callback"
            f"?state={state}&error=access_denied"
            "&error_description=user+said+no+because+reasons"
        )
        assert "reason=provider_denied" in r.text
        # Provider error text must NOT be echoed into the deep link page.
        assert "because+reasons" not in r.text
        assert "said no" not in r.text

    def test_exchange_failure_is_coarse(self, broker_env, monkeypatch):
        import app.api.routes.portal_connectors as routes
        from app.services.portal.connector_oauth_service import (
            TokenExchangeFailed,
        )

        def _boom(**kwargs):
            raise TokenExchangeFailed("provider said: invalid_grant XYZZY")

        monkeypatch.setattr(
            routes.connector_oauth_service, "exchange_code_for_tokens", _boom
        )
        env = broker_env
        state = self._put_state(env, env["user_a"])
        r = env["client"].get(
            f"/portal/connectors/oauth/callback?code=abc&state={state}"
        )
        assert "reason=exchange_failed" in r.text
        assert "XYZZY" not in r.text


# ---------------------------------------------------------------------------
# Token fetch (mcp-gateway path)
# ---------------------------------------------------------------------------


class TestTokenFetch:
    def test_returns_access_never_refresh(self, broker_env):
        env = broker_env
        _seed_row(
            env,
            user_id=env["user_a"],
            access_token="tok_live",
            refresh_token="tok_refresh_hidden",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        env["login_as"](env["user_a"])
        r = env["client"].post("/portal/connectors/github/token")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] == "tok_live"
        assert "refresh_token" not in body
        assert "tok_refresh_hidden" not in r.text

    def test_refreshes_when_expired(self, broker_env, monkeypatch):
        import app.api.routes.portal_connectors as routes
        from app.models.connector_token import ConnectorTokenORM
        from app.services.portal.connector_oauth_service import decrypt_token

        def _fake_refresh(**kwargs):
            assert kwargs["refresh_token"] == "tok_refresh"
            return {
                "access_token": "tok_fresh",
                "refresh_token": None,
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                "scopes": "repo",
            }

        monkeypatch.setattr(
            routes.connector_oauth_service, "refresh_access_token", _fake_refresh
        )
        env = broker_env
        _seed_row(
            env,
            user_id=env["user_a"],
            access_token="tok_stale",
            refresh_token="tok_refresh",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env["login_as"](env["user_a"])
        r = env["client"].post("/portal/connectors/github/token")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] == "tok_fresh"
        assert "refresh_token" not in body

        s = env["Session"]()
        row = s.query(ConnectorTokenORM).filter_by(
            portal_user_id=env["user_a"], connector_id="github"
        ).one()
        assert decrypt_token(row.access_token_encrypted) == "tok_fresh"
        # A refresh response without a new refresh token keeps the old one.
        assert decrypt_token(row.refresh_token_encrypted) == "tok_refresh"
        s.close()

    def test_expired_without_refresh_is_401(self, broker_env):
        env = broker_env
        _seed_row(
            env,
            user_id=env["user_a"],
            access_token="tok_dead",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        env["login_as"](env["user_a"])
        r = env["client"].post("/portal/connectors/github/token")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "connector_token_expired"

    def test_cross_user_is_404(self, broker_env):
        env = broker_env
        _seed_row(env, user_id=env["user_a"])
        env["login_as"](env["user_b"])
        r = env["client"].post("/portal/connectors/github/token")
        assert r.status_code == 404
        # And B's connection list doesn't show A's row.
        r2 = env["client"].get("/portal/connectors")
        assert r2.json()["connections"] == []


# ---------------------------------------------------------------------------
# List + disconnect
# ---------------------------------------------------------------------------


class TestListAndDisconnect:
    def test_list_shape(self, broker_env):
        env = broker_env
        _seed_row(
            env,
            user_id=env["user_a"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        env["login_as"](env["user_a"])
        r = env["client"].get("/portal/connectors")
        assert r.status_code == 200
        conns = r.json()["connections"]
        assert len(conns) == 1
        assert set(conns[0]) == {
            "connector_id", "provider", "connected", "expires_at", "scopes",
        }
        assert conns[0]["connected"] is True
        # No token material in the listing.
        assert "tok_" not in r.text

    def test_disconnect_deletes_and_revokes(self, broker_env, monkeypatch):
        import app.api.routes.portal_connectors as routes
        from app.models.connector_token import ConnectorTokenORM

        revoked: list[str] = []
        monkeypatch.setattr(
            routes.connector_oauth_service,
            "revoke_token_best_effort",
            lambda **kw: revoked.append(kw["connector_id"]),
        )
        env = broker_env
        _seed_row(env, user_id=env["user_a"])
        env["login_as"](env["user_a"])
        r = env["client"].delete("/portal/connectors/github")
        assert r.status_code == 200

        s = env["Session"]()
        assert (
            s.query(ConnectorTokenORM)
            .filter_by(portal_user_id=env["user_a"])
            .count()
            == 0
        )
        s.close()
        assert revoked == ["github"]

    def test_disconnect_cross_user_is_404(self, broker_env):
        env = broker_env
        _seed_row(env, user_id=env["user_a"])
        env["login_as"](env["user_b"])
        assert (
            env["client"].delete("/portal/connectors/github").status_code == 404
        )
