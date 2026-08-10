"""Phase 0 — Google SSO domain lock + central tenant + password kill-switch.

Server-side enforcement tests for the locked decisions:

* ``login_or_create`` rejects unverified emails (``email_not_verified``)
  and emails outside ``ALLOWED_EMAIL_DOMAINS`` (``domain_not_allowed``)
  BEFORE any account lookup — so the gates cover the link-onto-existing-
  account branch, not just create.
* New OAuth users join the single central tenant (idempotent by slug),
  never a personal tenant — also satisfies portal_users.tenant_id
  NOT NULL from migration 030.
* Empty allowlist = allow every domain (the future "open to gmail"
  mode is an env edit).
* ``PASSWORD_LOGIN_ENABLED=false`` turns /portal/auth/login + /signup
  into 403 ``password_login_disabled`` server-side.
* ``POST /portal/auth/oauth/verify`` is rate-limited per IP.
"""

from __future__ import annotations

import fakeredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.exceptions import AuthorizationError
from app.models.portal_user import PortalUserORM
from app.models.tenant import TenantORM
from app.services.portal import oauth_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    from app.core.db import Base
    import app.models  # noqa: F401 — register all tables

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def gbtech_only(monkeypatch):
    monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
    monkeypatch.setattr(settings, "central_tenant_slug", "gbtech")
    monkeypatch.setattr(settings, "central_tenant_name", "GB Tech")


def _oauth_login(db, *, email, verified=True, sub="sub-1", provider="google"):
    return oauth_service.login_or_create(
        db,
        provider=provider,
        oauth_id=sub,
        email=email,
        display_name="Test User",
        avatar_url=None,
        email_verified=verified,
    )


# ---------------------------------------------------------------------------
# Gate: email_verified
# ---------------------------------------------------------------------------


class TestEmailVerifiedGate:
    def test_unverified_email_denied(self, db_session, gbtech_only):
        with pytest.raises(AuthorizationError) as exc:
            _oauth_login(db_session, email="user@gbtech.co.th", verified=False)
        assert exc.value.code == "email_not_verified"
        # No account row was created.
        assert (
            db_session.execute(select(PortalUserORM)).scalars().first() is None
        )

    def test_default_fails_closed(self, db_session, gbtech_only):
        """A caller that forgets email_verified must be denied, not let in."""
        with pytest.raises(AuthorizationError):
            oauth_service.login_or_create(
                db_session,
                provider="google",
                oauth_id="sub-x",
                email="user@gbtech.co.th",
                display_name=None,
                avatar_url=None,
            )

    def test_unverified_cannot_link_existing_account(
        self, db_session, gbtech_only
    ):
        """The gate must fire BEFORE the email-link branch."""
        existing = PortalUserORM(
            id="existing-1",
            email="victim@gbtech.co.th",
            password_hash="x",
            status="active",
            email_verified=True,
            tenant_id="t-fixture",
        )
        db_session.add(existing)
        db_session.flush()

        with pytest.raises(AuthorizationError) as exc:
            _oauth_login(
                db_session, email="victim@gbtech.co.th", verified=False
            )
        assert exc.value.code == "email_not_verified"
        db_session.refresh(existing)
        assert existing.oauth_provider is None  # not linked


# ---------------------------------------------------------------------------
# Gate: domain allowlist
# ---------------------------------------------------------------------------


class TestDomainAllowlist:
    def test_disallowed_domain_denied(self, db_session, gbtech_only):
        with pytest.raises(AuthorizationError) as exc:
            _oauth_login(db_session, email="user@gmail.com")
        assert exc.value.code == "domain_not_allowed"
        assert (
            db_session.execute(select(PortalUserORM)).scalars().first() is None
        )

    def test_allowed_domain_accepted(self, db_session, gbtech_only):
        result = _oauth_login(db_session, email="user@gbtech.co.th")
        assert result["user"]["email"] == "user@gbtech.co.th"

    def test_empty_allowlist_allows_any_domain(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "allowed_email_domains", "")
        result = _oauth_login(db_session, email="anyone@gmail.com")
        assert result["user"]["email"] == "anyone@gmail.com"

    def test_multi_domain_and_at_prefix_tolerated(
        self, db_session, monkeypatch
    ):
        """Comma-separated list; '@'-prefixed entries don't lock everyone out."""
        monkeypatch.setattr(
            settings, "allowed_email_domains", "@gbtech.co.th, Gmail.com"
        )
        _oauth_login(db_session, email="a@gbtech.co.th", sub="s1")
        _oauth_login(db_session, email="b@gmail.com", sub="s2")

    def test_disallowed_domain_cannot_link_existing_account(
        self, db_session, gbtech_only
    ):
        """A pre-allowlist account on a now-banned domain can't OAuth in."""
        existing = PortalUserORM(
            id="legacy-1",
            email="old@gmail.com",
            password_hash="x",
            status="active",
            email_verified=True,
            tenant_id="t-fixture",
        )
        db_session.add(existing)
        db_session.flush()

        with pytest.raises(AuthorizationError) as exc:
            _oauth_login(db_session, email="old@gmail.com")
        assert exc.value.code == "domain_not_allowed"
        db_session.refresh(existing)
        assert existing.oauth_provider is None


# ---------------------------------------------------------------------------
# Central tenant
# ---------------------------------------------------------------------------


class TestCentralTenant:
    def test_new_oauth_user_joins_central_tenant(
        self, db_session, gbtech_only
    ):
        result = _oauth_login(db_session, email="user@gbtech.co.th")
        user = db_session.get(PortalUserORM, result["user"]["id"])
        assert user.tenant_id is not None
        tenant = db_session.execute(
            select(TenantORM).where(TenantORM.tenant_id == user.tenant_id)
        ).scalar_one()
        assert tenant.slug == "gbtech"
        assert tenant.name == "GB Tech"
        assert user.tenant_role == "member"
        assert user.token_version == 1
        assert bool(user.email_verified) is True

    def test_central_tenant_reused_not_duplicated(
        self, db_session, gbtech_only
    ):
        r1 = _oauth_login(db_session, email="a@gbtech.co.th", sub="s1")
        r2 = _oauth_login(db_session, email="b@gbtech.co.th", sub="s2")
        u1 = db_session.get(PortalUserORM, r1["user"]["id"])
        u2 = db_session.get(PortalUserORM, r2["user"]["id"])
        assert u1.tenant_id == u2.tenant_id
        tenants = db_session.execute(select(TenantORM)).scalars().all()
        assert len(tenants) == 1

    def test_create_race_reuses_winner_tenant(self, db_session, gbtech_only, monkeypatch):
        """A2: two concurrent first-logins must not 500 on the slug race.

        Simulated by pre-inserting the central tenant (the 'winner')
        while patching the lookup to report it missing — the loser's
        INSERT hits the unique slug constraint and must recover by
        re-selecting the winner's row.
        """
        winner = TenantORM(
            tenant_id="winner-tenant",
            name="GB Tech",
            slug="gbtech",
            status="active",
            region="global",
            ner_enabled=False,
        )
        db_session.add(winner)
        db_session.flush()

        monkeypatch.setattr(
            oauth_service, "_find_central_tenant", lambda _db, _slug: None
        )
        result = _oauth_login(db_session, email="race@gbtech.co.th")
        user = db_session.get(PortalUserORM, result["user"]["id"])
        assert user.tenant_id == "winner-tenant"
        tenants = db_session.execute(select(TenantORM)).scalars().all()
        assert len(tenants) == 1

    def test_link_keeps_existing_tenant(self, db_session, gbtech_only):
        """Linking onto a password account must not re-tenant the user."""
        tenant = TenantORM(
            tenant_id="own-tenant",
            name="Own Team",
            slug="own-team",
            status="active",
            region="global",
            ner_enabled=False,
        )
        existing = PortalUserORM(
            id="pw-user-1",
            email="dev@gbtech.co.th",
            password_hash="x",
            status="active",
            email_verified=True,
            tenant_id="own-tenant",
            tenant_role="owner",
        )
        db_session.add_all([tenant, existing])
        db_session.flush()

        result = _oauth_login(db_session, email="dev@gbtech.co.th")
        assert result["user"]["id"] == "pw-user-1"
        db_session.refresh(existing)
        assert existing.oauth_provider == "google"
        assert existing.tenant_id == "own-tenant"
        assert existing.tenant_role == "owner"


# ---------------------------------------------------------------------------
# Route-level: password kill-switch + signup allowlist + oauth/verify limiter
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_db_session
    from app.api.routes import portal_auth as portal_auth_module
    from app.api.routes.portal_auth import (
        get_portal_login_rate_limiter,
        get_portal_oauth_verify_rate_limiter,
        get_portal_signup_rate_limiter,
        router as portal_auth_router,
    )
    from app.core.db import Base
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )
    from app.services.admin.login_service import LoginRateLimiter
    from app.services.portal.oauth_flow_service import (
        OAuthStateStore,
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

    def _limiter(email_prefix: str, ip_prefix: str) -> LoginRateLimiter:
        return LoginRateLimiter(
            redis_client=fakeredis.FakeRedis(decode_responses=True),
            per_email_limit=100,
            per_email_window_seconds=60,
            per_ip_limit=100,
            per_ip_window_seconds=60,
            email_key_prefix=email_prefix,
            ip_key_prefix=ip_prefix,
        )

    oauth_verify_limiter = LoginRateLimiter(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        per_email_limit=3,
        per_email_window_seconds=60,
        per_ip_limit=3,
        per_ip_window_seconds=60,
        email_key_prefix="t_ov:",
        ip_key_prefix="t_ov_ip:",
    )

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_portal_login_rate_limiter] = lambda: _limiter(
        "t_l:", "t_l_ip:"
    )
    app.dependency_overrides[get_portal_signup_rate_limiter] = lambda: _limiter(
        "t_s:", "t_s_ip:"
    )
    app.dependency_overrides[get_portal_oauth_verify_rate_limiter] = (
        lambda: oauth_verify_limiter
    )
    state_store = OAuthStateStore(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        ttl_seconds=300,
    )
    app.dependency_overrides[get_oauth_state_store] = lambda: state_store
    app.include_router(portal_auth_router, prefix="/portal/auth")
    return TestClient(app), state_store


class TestPasswordKillSwitch:
    def test_login_403_when_disabled(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(settings, "password_login_enabled", False)
        r = c.post(
            "/portal/auth/login",
            json={"email": "user@gbtech.co.th", "password": "irrelevant1"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "password_login_disabled"

    def test_signup_403_when_disabled(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(settings, "password_login_enabled", False)
        r = c.post(
            "/portal/auth/signup",
            json={"email": "user@gbtech.co.th", "password": "longenough1"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "password_login_disabled"

    def test_login_open_when_enabled(self, client, monkeypatch):
        """Gate off → unknown creds fall through to a normal 401."""
        c, _ = client
        monkeypatch.setattr(settings, "password_login_enabled", True)
        r = c.post(
            "/portal/auth/login",
            json={"email": "nobody@gbtech.co.th", "password": "wrongwrong1"},
        )
        assert r.status_code == 401

    def test_public_settings_exposes_flag(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(settings, "password_login_enabled", False)
        r = c.get("/portal/auth/public-settings")
        assert r.status_code == 200
        assert r.json()["password_login_enabled"] is False


class TestSignupDomainLock:
    def test_signup_disallowed_domain_403(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(settings, "password_login_enabled", True)
        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        r = c.post(
            "/portal/auth/signup",
            json={"email": "user@gmail.com", "password": "longenough1"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "domain_not_allowed"

    def test_signup_allowed_domain_succeeds(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(settings, "password_login_enabled", True)
        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        r = c.post(
            "/portal/auth/signup",
            json={"email": "new@gbtech.co.th", "password": "longenough1"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["user"]["email"] == "new@gbtech.co.th"


class TestOAuthVerifyRateLimit:
    def test_verify_rate_limited_per_ip(self, client, monkeypatch):
        c, _ = client
        from app.api.routes import portal_auth as pa
        from app.services.portal.oauth_flow_service import (
            IdTokenVerificationFailed,
        )

        def _always_fail(_token):
            raise IdTokenVerificationFailed("forged")

        monkeypatch.setattr(pa, "verify_google_id_token", _always_fail)

        payload = {"provider": "google", "id_token": "x" * 32}
        # Limit is 3/window in the fixture: three failing attempts pass
        # the pre-check (and are recorded), the fourth is refused.
        for _ in range(3):
            r = c.post("/portal/auth/oauth/verify", json=payload)
            assert r.status_code == 422
        r = c.post("/portal/auth/oauth/verify", json=payload)
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "oauth_verify_rate_limited"

    def test_verify_passes_email_verified_to_policy(self, client, monkeypatch):
        """An id_token whose email_verified=false must be rejected 403."""
        c, _ = client
        from app.api.routes import portal_auth as pa

        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        monkeypatch.setattr(
            pa,
            "verify_google_id_token",
            lambda _t: {
                "email": "user@gbtech.co.th",
                "sub": "g-1",
                "name": "U",
                "picture": None,
                "email_verified": False,
                "hd": "gbtech.co.th",
            },
        )
        r = c.post(
            "/portal/auth/oauth/verify",
            json={"provider": "google", "id_token": "y" * 32},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "email_not_verified"

    def test_verify_happy_path_creates_central_tenant_user(
        self, client, monkeypatch
    ):
        c, _ = client
        from app.api.routes import portal_auth as pa

        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        monkeypatch.setattr(settings, "central_tenant_slug", "gbtech")
        monkeypatch.setattr(
            pa,
            "verify_google_id_token",
            lambda _t: {
                "email": "fresh@gbtech.co.th",
                "sub": "g-2",
                "name": "Fresh",
                "picture": None,
                "email_verified": True,
                "hd": "gbtech.co.th",
            },
        )
        r = c.post(
            "/portal/auth/oauth/verify",
            json={"provider": "google", "id_token": "z" * 32},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user"]["email"] == "fresh@gbtech.co.th"
        # /oauth/verify still returns the token pair in the body — the
        # NextAuth bridge needs them for /cookie-exchange.
        assert body["access_token"]
        assert body["refresh_token"]


class TestOAuthCallbackRouteGates:
    """A3 — the PKCE path (GET /oauth/callback) hits the same gates.

    The gates live in login_or_create (service level, already covered);
    these tests pin the ROUTE wiring — the callback must forward the
    verified claims (email_verified) so an unverified or off-domain
    identity gets a 403, not a session.
    """

    def _claims(self, **over):
        base = {
            "email": "cb@gbtech.co.th",
            "sub": "cb-sub-1",
            "name": "CB",
            "picture": None,
            "email_verified": True,
            "hd": "gbtech.co.th",
        }
        base.update(over)
        return base

    def _callback(self, client, store, monkeypatch, claims):
        from app.services.portal import oauth_flow_service

        monkeypatch.setattr(
            oauth_flow_service,
            "exchange_code_for_verified_userinfo",
            lambda **kw: claims,
        )
        state = store.put(
            provider="google", code_verifier="v" * 64, return_to="/"
        )
        return client.get(
            f"/portal/auth/oauth/callback?code=abc&state={state}",
            follow_redirects=False,
        )

    def test_unverified_claims_rejected_403(self, client, monkeypatch):
        c, store = client
        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        r = self._callback(
            c, store, monkeypatch, self._claims(email_verified=False)
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "email_not_verified"

    def test_offdomain_claims_rejected_403(self, client, monkeypatch):
        c, store = client
        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        r = self._callback(
            c,
            store,
            monkeypatch,
            self._claims(email="outsider@gmail.com", hd=None),
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "domain_not_allowed"

    def test_allowed_claims_pass(self, client, monkeypatch):
        c, store = client
        monkeypatch.setattr(settings, "allowed_email_domains", "gbtech.co.th")
        monkeypatch.setattr(settings, "central_tenant_slug", "gbtech")
        r = self._callback(c, store, monkeypatch, self._claims())
        assert r.status_code == 200, r.text
        assert r.json()["user"]["email"] == "cb@gbtech.co.th"
