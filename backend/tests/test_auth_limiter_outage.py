"""Phase 12 A4 — credential-guarding limiters never fail open on a
Redis outage.

Posture implemented:

* Admin login (``LoginRateLimiter``) and portal password reset ALWAYS
  fall back to the shared in-process conservative window when Redis is
  unreachable — regardless of ``RATE_LIMIT_REDIS_OUTAGE_MODE`` (that
  knob only governs general traffic limiters). Fail-open there is a
  free brute-force window.
* General traffic (chat request limiter) keeps the configurable
  default: fail-open on outage — availability beats throttling for
  chat when Redis is down, and upstream providers still enforce their
  own limits.

Fails-before: pre-change, ``LoginRateLimiter.check`` returned
``allowed=True`` unconditionally on a Redis exception (the 11th — and
1000th — attempt passed), and ``_rate_limit_check_and_bump`` returned
without counting (the 4th reset request passed). Revert either outage
branch and the corresponding tests below fail.
"""

from __future__ import annotations

import bcrypt
import pytest

import app.core.config as cfg
from app.core.exceptions import RateLimitExceededError
from app.services.admin.login_service import LoginRateLimiter
from app.services.portal import password_reset_service
from app.services.rate_limit_service import (
    _CONSERVATIVE_LIMIT,
    _conservative_hits,
    RateLimitService,
)


class _RaisingRedis:
    """Stands in for a down Redis — every op raises."""

    def get(self, *_a, **_k):
        raise ConnectionError("redis down")

    def incr(self, *_a, **_k):
        raise ConnectionError("redis down")

    def expire(self, *_a, **_k):
        raise ConnectionError("redis down")

    def ttl(self, *_a, **_k):
        raise ConnectionError("redis down")

    def delete(self, *_a, **_k):
        raise ConnectionError("redis down")

    def pipeline(self, *_a, **_k):
        raise ConnectionError("redis down")


@pytest.fixture(autouse=True)
def _reset_conservative_state():
    _conservative_hits.clear()
    yield
    _conservative_hits.clear()


def _login_limiter() -> LoginRateLimiter:
    return LoginRateLimiter(
        redis_client=_RaisingRedis(),
        per_email_limit=5,
        per_email_window_seconds=900,
        per_ip_limit=5,
        per_ip_window_seconds=900,
    )


class TestLoginLimiterOutage:
    def test_outage_throttles_after_conservative_limit(self, monkeypatch):
        """First N (conservative cap) attempts admitted, the (N+1)th —
        the 11th — denied. Pre-change every attempt was admitted."""
        monkeypatch.setattr(
            cfg.settings, "rate_limit_redis_outage_mode", "fail_open"
        )  # explicitly fail_open — must NOT apply to login
        rl = _login_limiter()
        verdicts = [
            rl.check(email="admin@example.com", ip="10.0.0.1").allowed
            for _ in range(_CONSERVATIVE_LIMIT + 3)
        ]
        assert verdicts[:_CONSERVATIVE_LIMIT] == [True] * _CONSERVATIVE_LIMIT
        assert verdicts[_CONSERVATIVE_LIMIT] is False, (
            "11th login attempt during a Redis outage must be throttled"
        )

    def test_outage_denial_carries_scope_and_retry_after(self):
        rl = _login_limiter()
        for _ in range(_CONSERVATIVE_LIMIT):
            rl.check(email="a@example.com", ip="10.0.0.2")
        decision = rl.check(email="a@example.com", ip="10.0.0.2")
        assert decision.allowed is False
        assert decision.scope in ("email", "ip")
        assert decision.retry_after_seconds > 0

    def test_outage_cap_is_per_scope_key(self):
        """A different account/IP pair keeps its own outage budget —
        the cap must not be a single global gate."""
        rl = _login_limiter()
        for _ in range(_CONSERVATIVE_LIMIT):
            rl.check(email="victim@example.com", ip="10.0.0.3")
        assert rl.check(email="victim@example.com", ip="10.0.0.3").allowed is False
        assert rl.check(email="other@example.com", ip="10.0.0.4").allowed is True

    def test_record_calls_still_never_raise_during_outage(self):
        rl = _login_limiter()
        rl.record_failure(email="x@example.com", ip="10.0.0.5")
        rl.record_success(email="x@example.com", ip="10.0.0.5")


class TestLoginRouteOutage429:
    @pytest.fixture
    def client(self, sqlite_engine):
        """Minimal app wiring mirroring test_admin_session_routes, with
        the RAISING-redis limiter injected."""
        import fakeredis
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import sessionmaker

        from app.api.deps import get_db_session
        from app.api.routes.admin_session import router as session_router
        from app.core.exceptions import GatewayError
        from app.middleware.error_handler import (
            handle_gateway_error,
            handle_unhandled_exception,
        )
        from app.services.admin.session_manager import SessionManager
        from app.services.admin.session_service import (
            get_login_rate_limiter,
            get_session_manager,
        )

        app = FastAPI()
        app.add_exception_handler(GatewayError, handle_gateway_error)
        app.add_exception_handler(Exception, handle_unhandled_exception)

        SessionLocal = sessionmaker(bind=sqlite_engine)

        def _db():
            s = SessionLocal()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        session_manager = SessionManager(
            redis_client=fakeredis.FakeRedis(decode_responses=True),
            signing_secrets=["test-signing-secret-abcdef0123456789"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        app.dependency_overrides[get_db_session] = _db
        app.dependency_overrides[get_session_manager] = lambda: session_manager
        app.dependency_overrides[get_login_rate_limiter] = _login_limiter
        app.include_router(session_router)
        return TestClient(app)

    @pytest.fixture
    def admin_user(self, db_session):
        import uuid

        from app.models.admin_user import AdminUserORM

        pw_hash = bcrypt.hashpw(
            b"CorrectHorseBattery9!", bcrypt.gensalt(rounds=4)
        ).decode("utf-8")
        user = AdminUserORM(
            id=str(uuid.uuid4()),
            username="alice",
            email="alice@example.com",
            hashed_password=pw_hash,
            role="admin",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        return user

    def test_11th_login_attempt_during_outage_is_429(self, client, admin_user):
        payload = {"email": "alice@example.com", "password": "wrong-password"}
        for i in range(_CONSERVATIVE_LIMIT):
            r = client.post("/auth/session/login", json=payload)
            assert r.status_code == 401, f"attempt {i + 1}: {r.text}"
        r = client.post("/auth/session/login", json=payload)
        assert r.status_code == 429, (
            "11th in-window login attempt during a Redis outage must 429 "
            f"(got {r.status_code})"
        )


class TestGeneralTrafficStillFailsOpen:
    def test_chat_limiter_fail_open_default_unchanged(self, monkeypatch):
        """The A4 change must NOT drag general traffic into conservative
        mode: with the default fail_open, chat request checks pass
        unlimited during an outage."""
        monkeypatch.setattr(
            cfg.settings, "rate_limit_redis_outage_mode", "fail_open"
        )
        svc = RateLimitService(redis_client=_RaisingRedis())
        for _ in range(_CONSERVATIVE_LIMIT * 5):
            assert svc.check_key("chat-key", limit=5)["allowed"] is True


class TestPasswordResetOutage:
    def test_outage_applies_same_3_per_hour_ceiling(self):
        redis = _RaisingRedis()
        for _ in range(3):
            password_reset_service._rate_limit_check_and_bump(
                redis, email_norm="outage-victim@example.com"
            )
        with pytest.raises(RateLimitExceededError):
            password_reset_service._rate_limit_check_and_bump(
                redis, email_norm="outage-victim@example.com"
            )

    def test_outage_ceiling_is_per_email(self):
        redis = _RaisingRedis()
        for _ in range(3):
            password_reset_service._rate_limit_check_and_bump(
                redis, email_norm="one@example.com"
            )
        # A different email still has its own budget.
        password_reset_service._rate_limit_check_and_bump(
            redis, email_norm="two@example.com"
        )
