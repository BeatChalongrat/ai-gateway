"""The page a connector's consent flow ends on knows which client began it.

Connecting a mailbox from the web chat used to end on "Returning to SRAN
Desktop…" with a ``sran://`` link — a page written for the desktop app, shown
to a person in a browser tab. The client now rides the ticket and the state
(an allow-list, never the callback's own query):

* ``web``: no desktop word, no ``sran://`` link; the page sends the person
  back to their SRAN Chat tab;
* ``desktop`` (and an older caller that says nothing): the deep link, as before;
* a replayed or expired state names no client: neutral words;
* every page is Thai and English, and carries no token or provider text.
"""

from __future__ import annotations

import re

import pytest

from tests.test_connector_oauth import broker_env  # noqa: F401 — the broker fixture


@pytest.fixture
def env(broker_env, monkeypatch):  # noqa: F811
    import app.api.routes.portal_connectors as routes

    monkeypatch.setattr(routes.connector_oauth_service, "client_credentials", lambda connector_id: ("cid", "csecret"))
    monkeypatch.setattr(routes.connector_oauth_service, "exchange_code_for_tokens",
                        lambda **kw: {"access_token": "tok_provider_SECRET", "refresh_token": None,
                                      "expires_at": None, "scopes": "repo"})
    broker_env["login_as"](broker_env["user_a"])
    return broker_env


def _finish(env, client: str | None, *, deny: bool = False):
    """ticket → start → callback, the way a browser walks it; returns the page the flow ends on."""
    q = f"?client={client}" if client else ""
    r = env["client"].post(f"/portal/connectors/github/ticket{q}")
    assert r.status_code == 200, r.text
    r1 = env["client"].get(r.json()["start_url"], follow_redirects=False)
    assert r1.status_code == 302
    state = re.search(r"[?&]state=([^&]+)", r1.headers["location"]).group(1)
    tail = "error=access_denied&error_description=user+said+no" if deny else "code=abc"
    return env["client"].get(f"/portal/connectors/oauth/callback?state={state}&{tail}")


class TestTheReturnPageKnowsItsClient:
    def test_from_the_web_the_page_never_names_the_desktop(self, env):
        page = _finish(env, "web")
        assert page.status_code == 200
        assert 'data-client="web"' in page.text and 'data-status="ok"' in page.text
        assert "Desktop" not in page.text
        assert "sran://" not in page.text
        assert "http-equiv" not in page.text                      # nothing bounces the tab anywhere
        assert "SRAN Chat" in page.text
        assert "ตรวจอีกครั้ง" in page.text and "Check again" in page.text   # the button the tab is waiting on

    def test_from_the_web_a_refusal_says_nothing_was_connected(self, env):
        page = _finish(env, "web", deny=True)
        assert 'data-client="web"' in page.text and 'data-status="error"' in page.text
        assert "Desktop" not in page.text and "sran://" not in page.text
        assert "provider_denied" in page.text                     # the coarse reason, for support
        assert "said no" not in page.text and "said+no" not in page.text
        assert "เชื่อมต่อไม่สำเร็จ" in page.text and "Connection failed" in page.text

    def test_from_the_desktop_the_page_returns_to_the_app(self, env):
        page = _finish(env, "desktop")
        assert 'data-client="desktop"' in page.text
        assert "sran://oauth/callback?connector=github&status=ok" in page.text
        assert 'http-equiv="refresh"' in page.text
        assert "SRAN Desktop" in page.text
        assert "กำลังกลับไปที่แอป SRAN Desktop" in page.text and "Returning to the SRAN Desktop app" in page.text

    def test_a_caller_that_says_nothing_is_an_older_desktop(self, env):
        page = _finish(env, None)
        assert 'data-client="desktop"' in page.text
        assert "sran://oauth/callback?connector=github&status=ok" in page.text

    def test_the_client_is_an_allow_list(self, env):
        r = env["client"].post("/portal/connectors/github/ticket?client=sran-evil")
        assert r.status_code in (400, 422)
        # …and a state row with anything else in it falls back to the default, never to a new scheme.
        state = env["state_store"].put({"user_id": env["user_a"], "tenant_id": None, "connector_id": "github",
                                        "code_verifier": "v" * 64, "client": "javascript"})
        page = env["client"].get(f"/portal/connectors/oauth/callback?code=abc&state={state}")
        assert 'data-client="desktop"' in page.text
        assert "javascript" not in page.text

    def test_a_replayed_link_names_no_client(self, env):
        page = env["client"].get("/portal/connectors/oauth/callback?code=abc&state=gone")
        assert 'data-client="unknown"' in page.text and 'data-status="error"' in page.text
        assert "http-equiv" not in page.text                      # nobody is bounced to an app they may not have
        assert "reason=invalid_state" in page.text                # the app link stays as a fallback only
        assert "ลิงก์นี้หมดอายุ" in page.text and "expired" in page.text

    @pytest.mark.parametrize("client", ["web", "desktop"])
    def test_no_page_carries_a_token(self, env, client):
        page = _finish(env, client)
        assert "tok_provider" not in page.text and "SECRET" not in page.text
        assert '<html lang="th">' in page.text
