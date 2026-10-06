"""The page a consent flow ends on is written for the person who began it.

The client already rides the ticket (test_connector_return_page.py). What was
still wrong for a person in a browser: one page in two languages at once, in
colours of no screen of theirs, telling them to close the tab, find the other
one and press a button. Now:

* the client says its screen's language (``lang``); the page is in that one
  language. A caller that says none (an older client) gets both, as before;
* the web client says where the person was (``return_to``); the page offers
  the way back there. Only a place of the web chat itself is ever linked: its
  origin must be one this deployment serves, its path under ``/chat``;
* the desktop page says what happens next — the app opens its Connectors
  page — because that is what the app now does with the link;
* no page carries a token or a provider's text, in any language.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest

from tests.test_connector_oauth import broker_env  # noqa: F401 — the broker fixture

THAI = re.compile(r"[฀-๿]")
WEB = "https://chat.example.test"


@pytest.fixture
def env(broker_env, monkeypatch):  # noqa: F811
    import app.api.routes.portal_connectors as routes
    from app.core.config import settings

    monkeypatch.setattr(routes.connector_oauth_service, "client_credentials", lambda connector_id: ("cid", "csecret"))
    monkeypatch.setattr(routes.connector_oauth_service, "exchange_code_for_tokens",
                        lambda **kw: {"access_token": "tok_provider_SECRET", "refresh_token": None,
                                      "expires_at": None, "scopes": "repo"})
    monkeypatch.setattr(settings, "cors_origins", f"{WEB},https://admin.example.test")
    broker_env["login_as"](broker_env["user_a"])
    return broker_env


def _finish(env, query: str, *, deny: bool = False, origin: str | None = WEB):
    headers = {"Origin": origin} if origin else {}
    r = env["client"].post(f"/portal/connectors/mail/ticket?{query}", headers=headers)
    assert r.status_code == 200, r.text
    r1 = env["client"].get(r.json()["start_url"], follow_redirects=False)
    assert r1.status_code == 302
    state = re.search(r"[?&]state=([^&]+)", r1.headers["location"]).group(1)
    tail = "error=access_denied&error_description=user+said+no" if deny else "code=abc"
    return env["client"].get(f"/portal/connectors/oauth/callback?state={state}&{tail}")


def _visible(html: str) -> str:
    """What a person reads: the body's text, without style or markup."""
    body = html.split("<body", 1)[1]
    return re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", " ", body, flags=re.S))


class TestThePageIsInTheLanguageOfTheScreenThatAsked:
    def test_web_english(self, env):
        page = _finish(env, "client=web&lang=en")
        assert '<html lang="en">' in page.text
        assert 'data-client="web"' in page.text and 'data-status="ok"' in page.text
        assert not THAI.search(_visible(page.text)), _visible(page.text)
        assert "Connected" in page.text and "SRAN Chat" in page.text
        assert "Desktop" not in page.text and "sran://" not in page.text

    def test_web_thai(self, env):
        page = _finish(env, "client=web&lang=th")
        assert '<html lang="th">' in page.text
        text = _visible(page.text)
        assert "เชื่อมต่อแล้ว" in text and "SRAN Chat" in text
        assert "Connected" not in text and "Close this tab" not in text and "Back to" not in text

    def test_desktop_english_says_the_app_opens_its_connectors_page(self, env):
        page = _finish(env, "client=desktop&lang=en")
        assert '<html lang="en">' in page.text
        assert not THAI.search(_visible(page.text))
        assert "sran://oauth/callback?connector=mail&amp;status=ok" in page.text or \
            "sran://oauth/callback?connector=mail&status=ok" in page.text
        assert 'http-equiv="refresh"' in page.text
        assert "Connectors page" in page.text and "SRAN Desktop" in page.text

    def test_desktop_thai(self, env):
        page = _finish(env, "client=desktop&lang=th")
        text = _visible(page.text)
        assert "หน้า Connectors" in text and "SRAN Desktop" in text
        assert "Returning" not in text and "Opening" not in text

    def test_a_refusal_in_the_screens_language(self, env):
        page = _finish(env, "client=web&lang=en", deny=True)
        assert 'data-status="error"' in page.text
        text = _visible(page.text)
        assert "Nothing was connected" in text and not THAI.search(text)
        assert "said no" not in page.text and "said+no" not in page.text

    def test_a_language_nobody_offers_is_refused_at_the_ticket(self, env):
        r = env["client"].post("/portal/connectors/mail/ticket?client=web&lang=fr")
        assert r.status_code in (400, 422)

    def test_a_caller_that_names_no_language_gets_both_as_before(self, env):
        page = _finish(env, "client=web")
        assert "เชื่อมต่อแล้ว" in page.text and "Connected" in page.text


class TestTheWebPageOffersTheWayBack:
    def test_back_to_where_the_person_was(self, env):
        back = f"{WEB}/chat/agents/1b2c?chat=main"
        page = _finish(env, f"client=web&lang=en&return_to={quote(back, safe='')}")
        assert f'href="{back.replace("&", "&amp;")}"' in page.text
        assert "Back to SRAN Chat" in page.text
        # the tab is not thrown anywhere by itself: the person presses the link
        assert "http-equiv" not in page.text and "<script" not in page.text

    def test_after_a_refusal_too(self, env):
        back = f"{WEB}/chat/agents/1b2c"
        page = _finish(env, f"client=web&lang=th&return_to={quote(back, safe='')}", deny=True)
        assert f'href="{back}"' in page.text and "กลับไปที่ SRAN Chat" in page.text

    @pytest.mark.parametrize("bad", [
        "https://evil.example/chat/agents/1",        # another origin
        "https://chat.example.test.evil.example/chat",  # looks like ours, is not
        "https://admin.example.test/accounts",   # an origin we serve, not a place of the chat
        "https://chat.example.test/portal/login",    # the chat's origin, not under /chat
        "//chat.example.test/chat",                  # no scheme
        "/chat/agents/1",                            # no origin at all
        "javascript:alert(1)",
        "https://user:pw@chat.example.test/chat",    # credentials in a link
        "https://chat.example.test/chat/\"><script>alert(1)</script>",
    ])
    def test_only_a_place_of_the_web_chat_is_ever_linked(self, env, bad):
        page = _finish(env, f"client=web&lang=en&return_to={quote(bad, safe='')}")
        assert page.status_code == 200
        assert "<a " not in page.text, page.text
        assert "evil" not in page.text and "javascript" not in page.text and "<script" not in page.text
        # without a way back it says what to do with the tab instead
        assert "Close this tab" in page.text

    def test_the_way_back_must_be_on_the_origin_that_asked(self, env):
        # a ticket asked from one origin cannot plant a link to another we happen to serve
        back = "https://admin.example.test/chat/x"
        page = _finish(env, f"client=web&lang=en&return_to={quote(back, safe='')}", origin=WEB)
        assert "<a " not in page.text

    def test_the_desktop_page_never_links_back_to_a_web_page(self, env):
        back = f"{WEB}/chat/agents/1b2c"
        page = _finish(env, f"client=desktop&lang=en&return_to={quote(back, safe='')}")
        assert back not in page.text


class TestNoPageCarriesWhatItMustNot:
    @pytest.mark.parametrize("query", ["client=web&lang=en", "client=web&lang=th", "client=desktop&lang=en", "client=desktop&lang=th"])
    def test_no_token_and_no_provider_text(self, env, query):
        for deny in (False, True):
            page = _finish(env, query, deny=deny)
            assert "tok_provider" not in page.text and "SECRET" not in page.text
            assert "access_denied" not in page.text and "said" not in page.text


class TestAStandInForTheProvider:
    """A deployment's test stack can point the Google connectors at a stand-in consent server."""

    def test_the_consent_and_token_endpoints_follow_the_settings(self, broker_env, monkeypatch):  # noqa: F811
        from app.core.config import settings
        from app.services.portal import connector_oauth_service as svc

        assert svc.endpoint("mail", "authorize_url") == "https://accounts.google.com/o/oauth2/v2/auth"
        assert svc.endpoint("mail", "token_url") == "https://oauth2.googleapis.com/token"
        monkeypatch.setattr(settings, "connector_oauth_google_authorize_url", "http://stand-in:8099/authorize")
        monkeypatch.setattr(settings, "connector_oauth_google_token_url", "http://stand-in:8099/token")
        assert svc.endpoint("mail", "authorize_url") == "http://stand-in:8099/authorize"
        assert svc.endpoint("gmail", "token_url") == "http://stand-in:8099/token"
        # another provider's connectors are untouched
        assert "stand-in" not in svc.endpoint("github", "authorize_url")
