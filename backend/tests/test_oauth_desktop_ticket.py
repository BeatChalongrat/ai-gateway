"""SSO Phase 2 — desktop server-side PKCE hand-off (ticket flow).

Route-level tests for the way-B desktop branch:

* ``GET /oauth/start?client=desktop`` records the surface in the state.
* ``GET /oauth/callback`` with a desktop state answers a 200 HTML
  landing page whose script/button fire
  ``sran://oauth/callback?ticket=<opaque>`` — NO token material
  anywhere in the page — after running the SAME login_or_create gates
  as web (unverified / off-domain identities get a branded error page,
  never a ticket).
* ``POST /oauth/desktop/exchange`` redeems the ticket exactly once
  WITH the nonce the app minted at /oauth/start (B0 binding - a
  hijacked sran:// link alone is worthless); replayed, unknown,
  expired, and nonce-mismatched tickets all get the same 401.

Only the provider round-trip (exchange_code_for_verified_userinfo) is
stubbed — state store, ticket store, gates, and routes are real.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.services.portal import oauth_flow_service
from app.services.portal.oauth_flow_service import (
    OAuthStateStore,
    OAuthTicketStore,
    get_oauth_state_store,
    get_oauth_ticket_store,
)


TEST_NONCE = "n" * 32  # 22-128 URL-safe chars per the start gate

GOOD_CLAIMS = {
    "email": "desktop-user@gbtech.co.th",
    "sub": "desktop-sub-1",
    "name": "Desktop User",
    "picture": None,
    "email_verified": True,
    "hd": "gbtech.co.th",
}


@pytest.fixture
def harness(monkeypatch):
    """App + stores wired to fakeredis and a fresh sqlite DB."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_db_session
    from app.api.routes.portal_auth import (
        get_portal_oauth_ticket_rate_limiter,
        router as portal_auth_router,
    )
    from app.core.db import Base
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )
    from app.services.admin.login_service import LoginRateLimiter
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

    state_store = OAuthStateStore(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        ttl_seconds=300,
    )
    ticket_store = OAuthTicketStore(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        ttl_seconds=60,
    )
    limiter = LoginRateLimiter(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        per_email_limit=100,
        per_email_window_seconds=60,
        per_ip_limit=100,
        per_ip_window_seconds=60,
        email_key_prefix="t_tk:",
        ip_key_prefix="t_tk_ip:",
    )

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_oauth_state_store] = lambda: state_store
    app.dependency_overrides[get_oauth_ticket_store] = lambda: ticket_store
    app.dependency_overrides[get_portal_oauth_ticket_rate_limiter] = (
        lambda: limiter
    )
    app.include_router(portal_auth_router, prefix="/portal/auth")

    monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
    monkeypatch.setattr(settings, "central_tenant_slug", "gbtech")
    monkeypatch.setattr(settings, "central_tenant_name", "GB Tech")
    monkeypatch.setattr(settings, "oauth_google_client_id", "test-client-id")

    return TestClient(app), state_store, ticket_store


def _desktop_callback(client, state_store, monkeypatch, claims):
    monkeypatch.setattr(
        oauth_flow_service,
        "exchange_code_for_verified_userinfo",
        lambda **kw: claims,
    )
    state = state_store.put(
        provider="google",
        code_verifier="v" * 64,
        return_to="/",
        client="desktop",
        nonce=TEST_NONCE,
    )
    return client.get(
        f"/portal/auth/oauth/callback?code=abc&state={state}",
        follow_redirects=False,
    )


class TestStartRecordsClient:
    def test_desktop_client_stored_in_state(self, harness):
        client, state_store, _ = harness
        r = client.get(
            "/portal/auth/oauth/start?provider=google&client=desktop"
            f"&nonce={TEST_NONCE}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"].startswith(
            "https://accounts.google.com/"
        )
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        row = state_store.pop(state)
        assert row is not None
        assert row["client"] == "desktop"
        assert row["nonce"] == TEST_NONCE

    def test_default_client_is_web(self, harness):
        client, state_store, _ = harness
        r = client.get(
            "/portal/auth/oauth/start?provider=google",
            follow_redirects=False,
        )
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        assert state_store.pop(state)["client"] == "web"

    def test_unknown_client_rejected(self, harness):
        client, _, _ = harness
        r = client.get(
            "/portal/auth/oauth/start?provider=google&client=mobile",
            follow_redirects=False,
        )
        assert r.status_code == 422

    def test_desktop_without_nonce_rejected(self, harness):
        client, _, _ = harness
        r = client.get(
            "/portal/auth/oauth/start?provider=google&client=desktop",
            follow_redirects=False,
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_nonce"

    def test_desktop_malformed_nonce_rejected(self, harness):
        client, _, _ = harness
        r = client.get(
            "/portal/auth/oauth/start?provider=google&client=desktop"
            "&nonce=short",
            follow_redirects=False,
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_nonce"

    def test_web_ignores_nonce(self, harness):
        """Backward-compat: web starts don't need (or keep) a nonce."""
        client, state_store, _ = harness
        r = client.get(
            f"/portal/auth/oauth/start?provider=google&nonce={TEST_NONCE}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        assert state_store.pop(state)["nonce"] is None


class TestDesktopCallbackTicketRedirect:
    def test_allowed_claims_render_landing_page_with_opaque_ticket_only(
        self, harness, monkeypatch
    ):
        client, state_store, ticket_store = harness
        r = _desktop_callback(client, state_store, monkeypatch, GOOD_CLAIMS)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        html = r.text

        # The page fires the deep link itself and offers a fallback button.
        links = re.findall(r"sran://oauth/callback\?ticket=([0-9a-f]+)", html)
        assert links, "landing page must carry the sran:// deep link"
        assert len(set(links)) == 1  # script + button use the SAME ticket
        ticket = links[0]
        assert "ปิดแท็บนี้" in html  # tells the user the tab is done

        # NOTHING but the opaque ticket: no tokens, no nonce, anywhere.
        entry = ticket_store.pop(ticket)
        assert entry["nonce"] == TEST_NONCE
        stored = entry["result"]
        assert stored["access_token"] and stored["refresh_token"]
        assert stored["access_token"] not in html
        assert stored["refresh_token"] not in html
        assert TEST_NONCE not in html
        assert stored["user"]["email"] == GOOD_CLAIMS["email"]

    def test_unverified_claims_rejected_no_ticket(self, harness, monkeypatch):
        client, state_store, ticket_store = harness
        claims = dict(GOOD_CLAIMS, email_verified=False)
        r = _desktop_callback(client, state_store, monkeypatch, claims)
        assert r.status_code == 403
        assert r.headers["content-type"].startswith("text/html")
        assert "sran://" not in r.text  # no ticket for a rejected identity
        assert "ลงชื่อเข้าใช้ไม่สำเร็จ" in r.text

    def test_offdomain_claims_rejected_no_ticket(self, harness, monkeypatch):
        client, state_store, ticket_store = harness
        claims = dict(
            GOOD_CLAIMS, email="outsider@gmail.com", hd=None
        )
        r = _desktop_callback(client, state_store, monkeypatch, claims)
        assert r.status_code == 403
        assert r.headers["content-type"].startswith("text/html")
        assert "sran://" not in r.text
        assert "domain" in r.text  # gate message surfaces on the page

    def test_web_state_still_returns_json(self, harness, monkeypatch):
        """Phase 1 behaviour untouched: a web state gets the JSON body
        (the NextAuth-independent way-B web path), not a deep link."""
        client, state_store, _ = harness
        monkeypatch.setattr(
            oauth_flow_service,
            "exchange_code_for_verified_userinfo",
            lambda **kw: GOOD_CLAIMS,
        )
        state = state_store.put(
            provider="google", code_verifier="v" * 64, return_to="/"
        )
        r = client.get(
            f"/portal/auth/oauth/callback?code=abc&state={state}",
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert r.json()["user"]["email"] == GOOD_CLAIMS["email"]


class TestDesktopExchange:
    def _mint_ticket(self, client, state_store, monkeypatch):
        r = _desktop_callback(client, state_store, monkeypatch, GOOD_CLAIMS)
        return re.findall(r"sran://oauth/callback\?ticket=([0-9a-f]+)", r.text)[0]

    def test_valid_ticket_redeems_once(self, harness, monkeypatch):
        client, state_store, _ = harness
        ticket = self._mint_ticket(client, state_store, monkeypatch)
        r = client.post(
            "/portal/auth/oauth/desktop/exchange",
            json={"ticket": ticket, "nonce": TEST_NONCE},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["user"]["email"] == GOOD_CLAIMS["email"]
        # Tokens ride the body, not cookies — the desktop has no jar.
        assert "set-cookie" not in {k.lower() for k in r.headers}

    def test_replayed_ticket_rejected(self, harness, monkeypatch):
        client, state_store, _ = harness
        ticket = self._mint_ticket(client, state_store, monkeypatch)
        assert (
            client.post(
                "/portal/auth/oauth/desktop/exchange",
                json={"ticket": ticket, "nonce": TEST_NONCE},
            ).status_code
            == 200
        )
        r = client.post(
            "/portal/auth/oauth/desktop/exchange",
            json={"ticket": ticket, "nonce": TEST_NONCE},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_ticket"

    def test_unknown_ticket_rejected(self, harness):
        client, _, _ = harness
        r = client.post(
            "/portal/auth/oauth/desktop/exchange",
            json={"ticket": "f" * 64, "nonce": TEST_NONCE},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_ticket"

    def test_expired_ticket_rejected(self, harness):
        client, _, ticket_store = harness
        # A store with a 1s TTL on the same fakeredis — real expiry.
        short = OAuthTicketStore(
            redis_client=ticket_store._redis, ttl_seconds=1
        )
        ticket = short.put(
            {"access_token": "a", "refresh_token": "r"}, nonce=TEST_NONCE
        )
        time.sleep(1.2)
        r = client.post(
            "/portal/auth/oauth/desktop/exchange",
            json={"ticket": ticket, "nonce": TEST_NONCE},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_ticket"

    def test_wrong_nonce_rejected_and_burns_ticket(
        self, harness, monkeypatch
    ):
        """B0: a hijacker with the deep link but not the nonce gets the
        same 401 - and the attempt consumes the ticket, so the login
        fails visibly instead of being stolen."""
        client, state_store, _ = harness
        ticket = self._mint_ticket(client, state_store, monkeypatch)
        r = client.post(
            "/portal/auth/oauth/desktop/exchange",
            json={"ticket": ticket, "nonce": "x" * 32},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_ticket"
        # The real app retrying with the RIGHT nonce now also fails -
        # the ticket was burned by the hijack attempt (one-shot pop).
        r2 = client.post(
            "/portal/auth/oauth/desktop/exchange",
            json={"ticket": ticket, "nonce": TEST_NONCE},
        )
        assert r2.status_code == 401

    def test_missing_nonce_rejected_by_schema(self, harness, monkeypatch):
        client, state_store, _ = harness
        ticket = self._mint_ticket(client, state_store, monkeypatch)
        r = client.post(
            "/portal/auth/oauth/desktop/exchange", json={"ticket": ticket}
        )
        assert r.status_code == 422
