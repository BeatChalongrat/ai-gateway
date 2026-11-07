"""FIX134 quiet — three things the production log kept repeating.

B2  The OAuth ``return_to`` allowlist (``GET /portal/auth/oauth/start``) is
    built from ``PORTAL_ORIGIN`` / ``ADMIN_UI_ORIGIN``. Since FIX133 the UIs
    are served over https behind Caddy, so an https return_to must pass, and
    outside development an http:// origin in the allowlist refuses to boot.
B4  Moderation honours the OpenAI switch: no /v1/moderations call without a
    usable OpenAI key or with MODERATION_MODE=off; a 401/403 from the
    endpoint opens a breaker for the process (30 minutes) with ONE warning,
    not an INFO line with ``error=http_401`` on every turn.
C3  The email alert channel exists only when mail can actually leave:
    EMAIL_DELIVERY=disabled (or no Resend key in a mode that needs one)
    drops ``email`` from the configured channels, ops-watch says so once at
    start, and nothing schedules a retry for a channel that is off.
"""

from __future__ import annotations

import logging

import fakeredis
import httpx
import pytest
import respx

from app.core.config import Settings, settings
from app.services import alert_channels
from app.services import moderation_service as ms
from app.services.alert_channels import Notification
from app.services.email import sender
from app.services.moderation_service import (
    MODERATION_ENDPOINT,
    ModerationService,
    resolve_moderation_key,
)
from app.services.portal.oauth_flow_service import InvalidReturnUrl, validate_return_to

STRONG = "x" * 40


def _prod(**over) -> Settings:
    """Production settings that pass every other gate in validate_secrets."""
    base = dict(
        _env_file=None,
        app_env="production",
        jwt_secret=STRONG,
        master_encryption_key=STRONG,
        portal_jwt_secret=STRONG,
        service_jwt_secret=STRONG,
        internal_service_secret=STRONG,
        dlp_audit_key="a" * 64,
        session_signing_secrets=STRONG,
        provider_key_master_key="b" * 64,
        resend_api_key="",
        email_delivery="disabled",
        auth_cookie_samesite="lax",
        portal_origin="https://localhost:3001",
        admin_ui_origin="https://localhost:3000",
    )
    base.update(over)
    return Settings(**base)


class _Recorder:
    """Stands in for a module's structlog logger (``capture_logs`` misses
    loggers cached before another test reset the structlog config)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _rec(self, event, **kw):
        self.events.append({"event": event, **kw})

    info = warning = error = debug = exception = _rec


# ---------------------------------------------------------------------------
# B2 — https return_to allowlist
# ---------------------------------------------------------------------------


class TestB2ReturnToOrigins:
    def test_origins_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PORTAL_ORIGIN", "https://localhost:3001")
        monkeypatch.setenv("ADMIN_UI_ORIGIN", "https://localhost:3000")
        s = Settings(_env_file=None)
        assert s.portal_origin == "https://localhost:3001"
        assert s.admin_ui_origin == "https://localhost:3000"
        assert s.oauth_return_to_origins() == [
            "https://localhost:3001",
            "https://localhost:3000",
        ]

    def test_defaults_name_the_dev_stack_ports(self, monkeypatch):
        # 3001 = portal, 3000 = admin-ui (docker-compose.yml); the old
        # defaults pointed at 3010 and at the portal's port for the admin UI.
        monkeypatch.delenv("PORTAL_ORIGIN", raising=False)
        monkeypatch.delenv("ADMIN_UI_ORIGIN", raising=False)
        s = Settings(_env_file=None)
        assert s.portal_origin == "http://localhost:3001"
        assert s.admin_ui_origin == "http://localhost:3000"

    def test_trailing_slash_and_blank_entries_are_normalised(self):
        s = Settings(_env_file=None, portal_origin="https://localhost:3001/", admin_ui_origin="  ")
        assert s.oauth_return_to_origins() == ["https://localhost:3001"]

    def test_production_refuses_an_http_origin(self):
        with pytest.raises(RuntimeError) as ei:
            _prod(portal_origin="http://localhost:3001").validate_secrets()
        assert "PORTAL_ORIGIN" in str(ei.value)
        with pytest.raises(RuntimeError) as ei:
            _prod(admin_ui_origin="http://localhost:3000").validate_secrets()
        assert "ADMIN_UI_ORIGIN" in str(ei.value)
        _prod().validate_secrets()  # https on both: fine
        _prod(admin_ui_origin="").validate_secrets()  # empty = no absolute return_to to that UI

    def test_production_refuses_a_value_that_is_not_an_origin(self):
        # The allowlist compares scheme://host[:port] — a path or query in
        # the setting would never match and silently reject every return_to.
        with pytest.raises(RuntimeError) as ei:
            _prod(portal_origin="https://localhost:3001/chat").validate_secrets()
        assert "PORTAL_ORIGIN" in str(ei.value)

    def test_development_keeps_http_origins(self):
        s = Settings(_env_file=None, app_env="development", portal_origin="http://localhost:3001")
        s.validate_secrets()

    def test_https_return_to_is_accepted_by_the_allowlist(self):
        s = Settings(
            _env_file=None,
            portal_origin="https://localhost:3001",
            admin_ui_origin="https://localhost:3000",
        )
        allowed = s.oauth_return_to_origins()
        assert validate_return_to("https://localhost:3001/app", allowed_origins=allowed) == "https://localhost:3001/app"
        assert validate_return_to("https://localhost:3000/keys", allowed_origins=allowed) == "https://localhost:3000/keys"
        with pytest.raises(InvalidReturnUrl):
            validate_return_to("http://localhost:3001/app", allowed_origins=allowed)  # the old scheme
        with pytest.raises(InvalidReturnUrl):
            validate_return_to("https://localhost:3002/chat", allowed_origins=allowed)  # not listed

    @pytest.fixture
    def oauth_client(self, monkeypatch):
        """The portal_auth router with a fakeredis state store (as in
        test_oauth_desktop_ticket) — enough for GET /oauth/start."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.routes.portal_auth import router as portal_auth_router
        from app.core.exceptions import GatewayError
        from app.middleware.error_handler import handle_gateway_error, handle_unhandled_exception
        from app.services.portal.oauth_flow_service import OAuthStateStore, get_oauth_state_store

        app = FastAPI()
        app.add_exception_handler(GatewayError, handle_gateway_error)
        app.add_exception_handler(Exception, handle_unhandled_exception)
        store = OAuthStateStore(redis_client=fakeredis.FakeRedis(decode_responses=True), ttl_seconds=300)
        app.dependency_overrides[get_oauth_state_store] = lambda: store
        app.include_router(portal_auth_router, prefix="/portal/auth")
        monkeypatch.setattr(settings, "oauth_google_client_id", "test-client-id")
        monkeypatch.setattr(settings, "portal_origin", "https://localhost:3001")
        monkeypatch.setattr(settings, "admin_ui_origin", "https://localhost:3000")
        return TestClient(app)

    def test_oauth_start_accepts_an_https_return_to(self, oauth_client):
        r = oauth_client.get(
            "/portal/auth/oauth/start?provider=google&return_to=https://localhost:3001/app",
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        assert r.headers["location"].startswith("https://accounts.google.com/")

    def test_oauth_start_still_rejects_an_unlisted_origin(self, oauth_client):
        r = oauth_client.get(
            "/portal/auth/oauth/start?provider=google&return_to=https://evil.example/steal",
            follow_redirects=False,
        )
        assert r.status_code != 302
        assert "invalid_return_to" in r.text


# ---------------------------------------------------------------------------
# B4 — moderation honours the OpenAI switch
# ---------------------------------------------------------------------------


class _Keys:
    """Stands in for ProviderKeyService: the UI-owned key store's answer."""

    def __init__(self, key: str | None) -> None:
        self.key = key
        self.calls = 0

    async def get_api_key_async(self, session, provider_name, model=None):
        assert provider_name == "openai"
        self.calls += 1
        return self.key


@pytest.fixture
def mod_logs(monkeypatch) -> list[dict]:
    rec = _Recorder()
    monkeypatch.setattr(ms, "logger", rec)
    return rec.events


def _disabled(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event"] == "moderation_disabled"]


def _ok_body() -> dict:
    return {"results": [{"flagged": False, "categories": {}, "category_scores": {}}]}


@pytest.mark.asyncio
class TestB4ModerationSwitch:
    @respx.mock
    async def test_no_call_and_one_warning_without_a_usable_key(self, mod_logs, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "moderation_mode", "auto", raising=False)
        route = respx.post(MODERATION_ENDPOINT).mock(return_value=httpx.Response(200, json=_ok_body()))
        keys = _Keys(None)  # the openai/admin key is is_active=false since FIX132
        assert await resolve_moderation_key(object(), keys) is None
        assert await resolve_moderation_key(object(), keys) is None
        assert keys.calls == 2
        assert not route.called
        warns = _disabled(mod_logs)
        assert len(warns) == 1 and warns[0]["reason"] == "no_usable_openai_key"

    @respx.mock
    async def test_a_usable_key_is_handed_over_and_the_env_key_is_the_fallback(self, mod_logs, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "moderation_mode", "auto", raising=False)
        assert await resolve_moderation_key(object(), _Keys("sk-ui")) == "sk-ui"
        monkeypatch.setattr(settings, "openai_api_key", "sk-env")
        assert await resolve_moderation_key(object(), _Keys(None)) == "sk-env"
        assert await resolve_moderation_key(None, _Keys("sk-ui")) == "sk-env"  # no session: env only
        assert _disabled(mod_logs) == []

    @respx.mock
    async def test_mode_off_makes_no_call_even_with_a_key(self, mod_logs, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-env")
        monkeypatch.setattr(settings, "moderation_mode", "off", raising=False)
        route = respx.post(MODERATION_ENDPOINT).mock(return_value=httpx.Response(200, json=_ok_body()))
        assert await resolve_moderation_key(object(), _Keys("sk-ui")) is None
        res = await ModerationService(timeout=1.0).check("x", "sk-ui")  # a direct caller too
        assert res.flagged is False and res.error == "moderation_off:moderation_mode_off"
        assert not route.called
        warns = _disabled(mod_logs)
        assert len(warns) == 1 and warns[0]["reason"] == "moderation_mode_off"

    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_key_opens_the_breaker_with_one_warning(self, status, mod_logs, monkeypatch):
        monkeypatch.setattr(settings, "moderation_mode", "auto", raising=False)
        route = respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(status, json={"error": {"message": "bad key"}})
        )
        svc = ModerationService(timeout=1.0)
        t0 = 1000.0
        monkeypatch.setattr(ms, "_clock", lambda: t0)

        first = await svc.check("x", "sk-dead")
        assert first.flagged is False and first.error == f"http_{status}"
        assert route.call_count == 1

        second = await svc.check("x", "sk-dead")  # the breaker: no request
        assert second.flagged is False and second.error == f"moderation_off:http_{status}"
        assert route.call_count == 1
        assert await resolve_moderation_key(object(), _Keys("sk-dead")) is None  # the turn skips it too
        assert route.call_count == 1

        warns = _disabled(mod_logs)
        assert len(warns) == 1
        assert warns[0]["reason"] == f"http_{status}"
        assert warns[0]["retry_after_s"] == ms.BREAKER_SECONDS == 1800

        # The window ends: one probe goes out again (the key may have been fixed).
        monkeypatch.setattr(ms, "_clock", lambda: t0 + ms.BREAKER_SECONDS + 1)
        third = await svc.check("x", "sk-dead")
        assert third.error == f"http_{status}"
        assert route.call_count == 2
        assert len(_disabled(mod_logs)) == 2  # one warning per trip, 30 minutes apart

    @respx.mock
    async def test_a_transient_error_does_not_open_the_breaker(self, mod_logs, monkeypatch):
        monkeypatch.setattr(settings, "moderation_mode", "auto", raising=False)
        route = respx.post(MODERATION_ENDPOINT).mock(return_value=httpx.Response(500, text="boom"))
        svc = ModerationService(timeout=1.0)
        assert (await svc.check("x", "sk-live")).error == "http_500"
        assert (await svc.check("x", "sk-live")).error == "http_500"
        assert route.call_count == 2
        assert _disabled(mod_logs) == []

    @respx.mock
    async def test_a_scan_that_works_re_arms_the_warning(self, mod_logs, monkeypatch):
        # Warn once per outage, not once per process: after moderation works
        # again, the next loss of the key is announced again.
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "moderation_mode", "auto", raising=False)
        respx.post(MODERATION_ENDPOINT).mock(return_value=httpx.Response(200, json=_ok_body()))
        assert await resolve_moderation_key(object(), _Keys(None)) is None
        assert len(_disabled(mod_logs)) == 1
        assert (await ModerationService(timeout=1.0).check("x", "sk-live")).error is None
        assert await resolve_moderation_key(object(), _Keys(None)) is None
        assert len(_disabled(mod_logs)) == 2

    def test_mode_is_validated_in_production(self):
        _prod(moderation_mode="off").validate_secrets()
        _prod(moderation_mode="AUTO").validate_secrets()
        with pytest.raises(RuntimeError) as ei:
            _prod(moderation_mode="maybe").validate_secrets()
        assert "MODERATION_MODE" in str(ei.value)


class TestB4ModerationOnTheTurn:
    """The orchestrator path (non-streaming): a profile with moderation on
    and no usable OpenAI key makes no moderation call and the turn goes on."""

    def test_no_moderation_call_without_a_key(self, monkeypatch):
        from fastapi.testclient import TestClient

        from app.main import app
        from app.services.policy.profiles import STRICT_PROFILE
        from app.services.provider_key_service import ProviderKeyService

        async def _strict(session, profile_id):
            return STRICT_PROFILE  # moderation_enabled=True

        monkeypatch.setattr("app.services.orchestrator.get_runtime_profile_async", _strict)
        monkeypatch.setattr("app.services.orchestrator.get_runtime_profile_fallback", lambda _pid: STRICT_PROFILE)
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "moderation_mode", "auto", raising=False)

        async def _keys(self, session, provider_name, model=None):
            return None if provider_name == "openai" else "test-stub-key"

        monkeypatch.setattr(ProviderKeyService, "get_api_key_async", _keys)

        called = {"n": 0}

        async def _check(self, text, api_key):
            called["n"] += 1
            return ms.ModerationResult(flagged=False)

        monkeypatch.setattr(ModerationService, "check", _check)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json={"model": "auto", "messages": [{"role": "user", "content": "hello there"}], "metadata": {}},
        )
        assert called["n"] == 0
        body = resp.json()
        assert (body.get("error") or {}).get("code") != "moderation_flagged"


# ---------------------------------------------------------------------------
# C3 — the email alert channel only when mail can leave
# ---------------------------------------------------------------------------


@pytest.fixture
def owner_channels(monkeypatch):
    monkeypatch.setattr(settings, "alert_environment", "")
    monkeypatch.setattr(settings, "alert_ntfy_url", "https://ntfy.example/owner")
    monkeypatch.setattr(settings, "alert_ntfy_token", "")
    monkeypatch.setattr(settings, "alert_email_to", "owner@example.com")
    monkeypatch.setattr(settings, "alert_webhook_url", "")
    monkeypatch.setattr(settings, "alert_test_ntfy_url", "", raising=False)


class TestC3EmailChannel:
    def test_channels_exclude_email_when_delivery_is_disabled(self, owner_channels, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "disabled")
        monkeypatch.setattr(settings, "resend_api_key", "re_x")
        assert alert_channels.configured_channels() == ["ntfy"]
        assert alert_channels.email_channel_off_reason() == "email_delivery_disabled"

    def test_channels_exclude_email_without_a_resend_key(self, owner_channels, monkeypatch):
        # ops-watch live: EMAIL_DELIVERY is not in its environment (auto),
        # APP_ENV=production, RESEND_API_KEY empty — Resend can never accept.
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "auto")
        monkeypatch.setattr(settings, "resend_api_key", "")
        assert alert_channels.configured_channels() == ["ntfy"]
        assert alert_channels.email_channel_off_reason() == "no_resend_api_key"

    def test_channels_include_email_when_it_can_be_sent(self, owner_channels, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "auto")
        monkeypatch.setattr(settings, "resend_api_key", "re_x")
        assert alert_channels.configured_channels() == ["ntfy", "email"]
        assert alert_channels.email_channel_off_reason() is None
        # dev / test: the console sender counts as delivery
        monkeypatch.setattr(settings, "app_env", "test")
        monkeypatch.setattr(settings, "resend_api_key", "")
        assert alert_channels.configured_channels() == ["ntfy", "email"]
        # a stated console sender anywhere
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "console")
        assert alert_channels.configured_channels() == ["ntfy", "email"]

    def test_ops_watch_says_once_at_start_that_email_is_off(self, owner_channels, monkeypatch):
        from app.ops import watch

        lines: list[dict] = []
        monkeypatch.setattr(watch, "_log", lambda msg, **kv: lines.append({"msg": msg, **kv}))
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "disabled")

        assert watch.log_email_channel_state() == "email_delivery_disabled"
        off = [l for l in lines if l["msg"] == "alert_email_channel_off"]
        assert len(off) == 1
        assert off[0]["reason"] == "email_delivery_disabled"

        # not configured, or on: nothing to say
        lines.clear()
        monkeypatch.setattr(settings, "alert_email_to", "")
        assert watch.log_email_channel_state() is None
        monkeypatch.setattr(settings, "alert_email_to", "owner@example.com")
        monkeypatch.setattr(settings, "email_delivery", "console")
        assert watch.log_email_channel_state() is None
        assert lines == []

    def test_deliver_never_touches_the_sender_when_email_is_off(self, owner_channels, monkeypatch, caplog):
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "auto")
        monkeypatch.setattr(settings, "resend_api_key", "")

        real_client = httpx.Client

        def _client(**kw):
            kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "x"}))
            return real_client(**kw)

        monkeypatch.setattr(alert_channels.httpx, "Client", _client)

        def _never(**kw):
            raise AssertionError("send_email must not be called for a channel that is off")

        monkeypatch.setattr(sender, "send_email", _never)
        monkeypatch.setattr(sender, "_send_via_resend", _never)
        monkeypatch.setattr(sender, "_retry_in_background", _never)

        with caplog.at_level(logging.INFO, logger="email.sender"):
            out = alert_channels.deliver(Notification(title="t", message="m", severity="warning"))
        assert out == {"ntfy": True}
        assert "email" not in out
        msgs = [r.getMessage() for r in caplog.records if r.name == "email.sender"]
        assert not any("email.resend_failure" in m or "email.retry_skipped_no_loop" in m for m in msgs)

    def test_send_email_without_a_key_does_not_retry(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "email_delivery", "auto")
        monkeypatch.setattr(settings, "resend_api_key", "")

        def _never(**kw):
            raise AssertionError("no Resend attempt / retry without a key")

        monkeypatch.setattr(sender, "_send_via_resend", _never)
        monkeypatch.setattr(sender, "_retry_in_background", _never)
        with caplog.at_level(logging.INFO, logger="email.sender"):
            res = sender.send_email(to="a@example.com", subject="s", html_body="<p>h</p>", text_body="t")
        assert res.delivered is False and res.reason == "no_resend_api_key"
        msgs = [r.getMessage() for r in caplog.records if r.name == "email.sender"]
        assert any("email.no_api_key" in m for m in msgs)
        assert not any("email.retry_skipped_no_loop" in m or "email.resend_failure" in m for m in msgs)
        assert sender.delivery_unavailable_reason() == "no_resend_api_key"


# ---------------------------------------------------------------------------
# B5 — password login allowlist (the smoke account while the switch is off)
# ---------------------------------------------------------------------------

SMOKE = "smoke@gbtech.co.th"
PASSWORD = "Smoke-Test-1234!"


@pytest.fixture
def portal(monkeypatch):
    """The real app with the portal tables reset and the settings the
    routes expect (as test_portal_auth does), plus one signed-up smoke
    account created while password login is still ON."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.main import app
    from app.models.credit_transaction import CreditTransactionORM
    from app.models.portal_user import PortalUserORM
    from app.models.system_setting import SystemSettingORM
    from tests.conftest import _integration_engine

    db = sessionmaker(bind=_integration_engine)()
    try:
        db.query(CreditTransactionORM).delete()
        db.query(PortalUserORM).delete()
        db.query(SystemSettingORM).delete()
        db.add_all([
            SystemSettingORM(key="free_trial_tokens", value="100000", description="tokens on signup"),
            SystemSettingORM(key="portal_signup_enabled", value="true", description="signup toggle"),
            SystemSettingORM(key="credit_low_threshold", value="10000", description="low warn"),
            SystemSettingORM(key="trial_model_tier", value="budget", description="tier"),
        ])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
    monkeypatch.setattr(settings, "password_login_enabled", True)
    monkeypatch.setattr(settings, "password_login_allowed_emails", "", raising=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/portal/auth/signup", json={"email": SMOKE, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return client


def _login(client, email: str, password: str = PASSWORD):
    return client.post("/portal/auth/login", json={"email": email, "password": password})


class TestB5PasswordLoginAllowlist:
    def test_allowlisted_email_signs_in_while_the_switch_is_off(self, portal, monkeypatch):
        from app.api.routes import portal_auth as pa

        rec = _Recorder()
        monkeypatch.setattr(pa, "logger", rec)
        monkeypatch.setattr(settings, "password_login_enabled", False)
        monkeypatch.setattr(settings, "password_login_allowed_emails", SMOKE, raising=False)
        r = _login(portal, SMOKE)
        assert r.status_code == 200, r.text
        assert r.json()["user"]["email"] == SMOKE
        assert r.cookies.get("portal_access_token")
        noted = [e for e in rec.events if e["event"] == "password_login_allowlisted"]
        assert len(noted) == 1
        assert noted[0]["email"] == "sm***@gbtech.co.th"  # masked: first 2 chars + ***@domain
        assert SMOKE not in str(rec.events)

    def test_everyone_else_still_gets_403(self, portal, monkeypatch):
        monkeypatch.setattr(settings, "password_login_enabled", False)
        monkeypatch.setattr(settings, "password_login_allowed_emails", SMOKE, raising=False)
        r = _login(portal, "someone@gbtech.co.th")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "password_login_disabled"

    def test_the_list_is_case_insensitive_and_tolerates_spaces(self, portal, monkeypatch):
        monkeypatch.setattr(settings, "password_login_enabled", False)
        monkeypatch.setattr(
            settings, "password_login_allowed_emails", " Other@Example.com , SMOKE@GBTech.co.th ", raising=False
        )
        assert settings.password_login_allowed_emails_set == {"other@example.com", SMOKE}
        assert _login(portal, "Smoke@GBTECH.co.th").status_code == 200

    def test_an_empty_list_changes_nothing(self, portal, monkeypatch):
        monkeypatch.setattr(settings, "password_login_enabled", False)
        monkeypatch.setattr(settings, "password_login_allowed_emails", "", raising=False)
        r = _login(portal, SMOKE)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "password_login_disabled"
        monkeypatch.setattr(settings, "password_login_allowed_emails", " , ", raising=False)
        assert _login(portal, SMOKE).status_code == 403

    def test_the_switch_on_still_admits_everyone(self, portal, monkeypatch):
        monkeypatch.setattr(settings, "password_login_enabled", True)
        monkeypatch.setattr(settings, "password_login_allowed_emails", "", raising=False)
        assert _login(portal, SMOKE).status_code == 200

    def test_signup_and_password_reset_stay_closed_for_the_allowlisted_address(self, portal, monkeypatch):
        monkeypatch.setattr(settings, "password_login_enabled", False)
        monkeypatch.setattr(settings, "password_login_allowed_emails", SMOKE, raising=False)
        r = portal.post("/portal/auth/signup", json={"email": "smoke2@gbtech.co.th", "password": PASSWORD})
        assert r.status_code == 403 and r.json()["error"]["code"] == "password_login_disabled"
        r = portal.post("/portal/auth/password-reset/request", json={"email": SMOKE})
        assert r.status_code == 403 and r.json()["error"]["code"] == "password_login_disabled"

    def test_the_setting_reads_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PASSWORD_LOGIN_ALLOWED_EMAILS", "Smoke@gbtech.co.th")
        s = Settings(_env_file=None)
        assert s.password_login_allowed_emails_set == {SMOKE}

    def test_mask(self):
        from app.api.routes.portal_auth import _mask_email

        assert _mask_email("smoke@gbtech.co.th") == "sm***@gbtech.co.th"
        assert _mask_email("a@x.io") == "a***@x.io"
