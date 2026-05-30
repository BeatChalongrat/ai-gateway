"""Phase 3.5 Commit 32 — password reset service + endpoint tests.

Covers:
- Request path: token minted + stored + email sent when email known;
  silent no-op + no email sent when unknown; no user-existence leak.
- Confirm path: valid token + valid password → bcrypt hash updated +
  token_version bumped + Redis key deleted.
- Token single-use: second confirm with the same token → 400
  invalid_token.
- Expired token: 400 invalid_token.
- Weak password: 400 weak_password with the concrete reason.
- End-to-end through the /portal/auth/password-reset/{request,confirm}
  endpoints.
"""

from __future__ import annotations

import time

import bcrypt
import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.portal_user import PortalUserORM
from app.models.credit_transaction import CreditTransactionORM
from app.services.portal import password_reset_service
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture()
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _clean_users():
    db = _Session()
    try:
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.commit()
        yield
    finally:
        db.close()


def _seed_user(
    *,
    email: str = "alice@example.com",
    password: str = "Initial1234",
    status: str = "active",
) -> PortalUserORM:
    db = _Session()
    try:
        user = PortalUserORM(
            id=f"u-{email.split('@')[0]}",
            email=email,
            password_hash=bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=4)
            ).decode("utf-8"),
            plan="free_trial",
            status=status,
            email_verified=True,
            credit_balance=0,
            credit_used=0,
            preferred_language="en",
            token_version=5,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service — request_reset
# ---------------------------------------------------------------------------


class TestRequestReset:
    def test_known_email_mints_token_and_stores_in_redis(
        self, redis_client
    ):
        _seed_user(email="a@example.com")
        db = _Session()
        try:
            result = password_reset_service.request_reset(
                db, redis_client, email="a@example.com"
            )
        finally:
            db.close()
        assert result["ok"] is True
        # Dev-mode echoes the token.
        token = result["token"]
        # Redis row exists.
        assert redis_client.get(f"password_reset:{token}") is not None

    def test_unknown_email_returns_ok_and_does_not_touch_redis(
        self, redis_client, monkeypatch
    ):
        sent = {"n": 0}

        def counting(**_kwargs):
            sent["n"] += 1
            from app.services.email.sender import EmailSendResult

            return EmailSendResult(delivered=True, provider="fake")

        monkeypatch.setattr(
            "app.services.email.send_password_reset_email", counting
        )
        db = _Session()
        try:
            result = password_reset_service.request_reset(
                db, redis_client, email="ghost@example.com"
            )
        finally:
            db.close()
        assert result == {"ok": True}
        assert sent["n"] == 0
        keys = list(redis_client.scan_iter("password_reset:*"))
        assert keys == []

    def test_inactive_user_no_email_no_token(self, redis_client, monkeypatch):
        _seed_user(email="dis@example.com", status="disabled")
        sent = {"n": 0}

        def counting(**_kwargs):
            sent["n"] += 1
            from app.services.email.sender import EmailSendResult

            return EmailSendResult(delivered=True, provider="fake")

        monkeypatch.setattr(
            "app.services.email.send_password_reset_email", counting
        )
        db = _Session()
        try:
            result = password_reset_service.request_reset(
                db, redis_client, email="dis@example.com"
            )
        finally:
            db.close()
        assert result == {"ok": True}
        assert sent["n"] == 0

    def test_malformed_email_is_silent_ok(self, redis_client):
        db = _Session()
        try:
            result = password_reset_service.request_reset(
                db, redis_client, email="not-an-email"
            )
        finally:
            db.close()
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Service — confirm_reset
# ---------------------------------------------------------------------------


class TestConfirmReset:
    def test_happy_path_updates_hash_and_bumps_token_version(
        self, redis_client
    ):
        user = _seed_user(email="bob@example.com")
        db = _Session()
        try:
            issued = password_reset_service.request_reset(
                db, redis_client, email="bob@example.com"
            )
            token = issued["token"]
            result = password_reset_service.confirm_reset(
                db,
                redis_client,
                token=token,
                new_password="BrandNew1234",
            )
            db.commit()
        finally:
            db.close()
        assert result == {"ok": True}

        # Verify the new hash verifies + old hash does NOT.
        db = _Session()
        try:
            fresh = db.get(PortalUserORM, user.id)
            assert bcrypt.checkpw(
                b"BrandNew1234", fresh.password_hash.encode("utf-8")
            )
            assert not bcrypt.checkpw(
                b"Initial1234", fresh.password_hash.encode("utf-8")
            )
            # Token-version bumped (was 5, now 6).
            assert fresh.token_version == 6
        finally:
            db.close()
        # Token deleted post-use.
        assert redis_client.get(f"password_reset:{token}") is None

    def test_unknown_token_rejected_as_invalid(self, redis_client):
        db = _Session()
        try:
            from app.core.exceptions import ValidationError

            with pytest.raises(ValidationError, match="expired"):
                password_reset_service.confirm_reset(
                    db,
                    redis_client,
                    token="not-a-token-anyone-minted",
                    new_password="StrongPass1",
                )
        finally:
            db.close()

    def test_single_use_second_attempt_fails(self, redis_client):
        _seed_user(email="single@example.com")
        db = _Session()
        try:
            issued = password_reset_service.request_reset(
                db, redis_client, email="single@example.com"
            )
            token = issued["token"]
            password_reset_service.confirm_reset(
                db, redis_client, token=token, new_password="FirstSet1234"
            )
            db.commit()
            from app.core.exceptions import ValidationError

            with pytest.raises(ValidationError, match="expired"):
                password_reset_service.confirm_reset(
                    db,
                    redis_client,
                    token=token,
                    new_password="SecondAttempt1234",
                )
        finally:
            db.close()

    def test_expired_token_rejected(self, redis_client):
        _seed_user(email="exp@example.com")
        db = _Session()
        try:
            issued = password_reset_service.request_reset(
                db, redis_client, email="exp@example.com"
            )
            token = issued["token"]
            # Hand-edit the Redis record to set an expired timestamp.
            import json
            key = f"password_reset:{token}"
            raw = redis_client.get(key)
            payload = json.loads(raw)
            payload["expires_at"] = time.time() - 60
            redis_client.set(key, json.dumps(payload))

            from app.core.exceptions import ValidationError

            with pytest.raises(ValidationError, match="expired"):
                password_reset_service.confirm_reset(
                    db, redis_client, token=token, new_password="StillStrong1",
                )
        finally:
            db.close()
        # Expired token is scrubbed from Redis so replay is impossible.
        assert redis_client.get(f"password_reset:{token}") is None

    def test_weak_password_rejected_before_token_spent(self, redis_client):
        _seed_user(email="w@example.com")
        db = _Session()
        try:
            issued = password_reset_service.request_reset(
                db, redis_client, email="w@example.com"
            )
            token = issued["token"]
            from app.core.exceptions import ValidationError

            with pytest.raises(ValidationError, match="8 characters"):
                password_reset_service.confirm_reset(
                    db, redis_client, token=token, new_password="short",
                )
            with pytest.raises(ValidationError, match="letter and one number"):
                password_reset_service.confirm_reset(
                    db, redis_client, token=token, new_password="onlyletters",
                )
        finally:
            db.close()
        # Token must still be live after a rejected weak-password attempt,
        # so the user can retry without re-requesting.
        assert redis_client.get(f"password_reset:{token}") is not None


# ---------------------------------------------------------------------------
# Endpoint — /portal/auth/password-reset/{request,confirm}
# ---------------------------------------------------------------------------


class TestEndpointRoundtrip:
    @pytest.fixture()
    def client(self, redis_client, monkeypatch):
        # Override the FastAPI Redis dep so every endpoint invocation
        # shares the in-process fakeredis.
        from app.api.routes import portal_auth

        def _fake_redis_dep():
            return redis_client

        app.dependency_overrides[portal_auth._portal_redis_client] = _fake_redis_dep
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(
                portal_auth._portal_redis_client, None
            )

    def test_request_and_confirm_end_to_end(self, client):
        user = _seed_user(email="flow@example.com")

        # Request
        r = client.post(
            "/portal/auth/password-reset/request",
            json={"email": "flow@example.com"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        token = body["token"]  # dev-mode exposure

        # Confirm with weak password
        r2 = client.post(
            "/portal/auth/password-reset/confirm",
            json={"token": token, "new_password": "short"},
        )
        assert r2.status_code in (400, 422)

        # Confirm with strong password
        r3 = client.post(
            "/portal/auth/password-reset/confirm",
            json={"token": token, "new_password": "EndToEnd1234"},
        )
        assert r3.status_code == 200

        # Try reusing the token — reject.
        r4 = client.post(
            "/portal/auth/password-reset/confirm",
            json={"token": token, "new_password": "AnotherTry1234"},
        )
        assert r4.status_code in (400, 422)

    def test_request_unknown_email_returns_200_same_shape(self, client):
        r = client.post(
            "/portal/auth/password-reset/request",
            json={"email": "nobody@example.com"},
        )
        assert r.status_code == 200
        body = r.json()
        # No token exposed for unknown emails even in dev mode.
        assert body == {"ok": True}


# ---------------------------------------------------------------------------
# Rate limiting — Commit 34
# ---------------------------------------------------------------------------


class TestRequestRateLimit:
    @pytest.fixture()
    def client(self, redis_client, monkeypatch):
        from app.api.routes import portal_auth

        def _fake_redis_dep():
            return redis_client

        app.dependency_overrides[portal_auth._portal_redis_client] = _fake_redis_dep
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(
                portal_auth._portal_redis_client, None
            )

    def test_fourth_request_in_window_is_rate_limited(
        self, client, redis_client
    ):
        """3 requests within an hour for the same email → all 200.
        4th → 429 with retry_after_seconds hint."""
        _seed_user(email="rl@example.com")
        for _ in range(3):
            r = client.post(
                "/portal/auth/password-reset/request",
                json={"email": "rl@example.com"},
            )
            assert r.status_code == 200
        r4 = client.post(
            "/portal/auth/password-reset/request",
            json={"email": "rl@example.com"},
        )
        assert r4.status_code == 429
        body = r4.json()
        assert body["error"]["code"] == "rate_limit_exceeded"

    def test_rate_limit_applies_to_unknown_emails_too(
        self, client, redis_client
    ):
        """A probe attack with a non-existent email must also trip
        the limiter so the attacker can't burn free requests hunting
        for which emails exist."""
        for _ in range(3):
            r = client.post(
                "/portal/auth/password-reset/request",
                json={"email": "unknown-probe@example.com"},
            )
            assert r.status_code == 200
        r4 = client.post(
            "/portal/auth/password-reset/request",
            json={"email": "unknown-probe@example.com"},
        )
        assert r4.status_code == 429

    def test_rate_limit_scoped_per_email(self, client, redis_client):
        """Different emails use separate counters."""
        for _ in range(3):
            client.post(
                "/portal/auth/password-reset/request",
                json={"email": "a-separate@example.com"},
            )
        # Exhausted for a-separate@; b-separate@ should still get 200.
        r = client.post(
            "/portal/auth/password-reset/request",
            json={"email": "b-separate@example.com"},
        )
        assert r.status_code == 200

    def test_rate_limit_redis_outage_fails_open(self, monkeypatch):
        """When Redis raises, the limiter must not block requests —
        the password check is the backstop and we don't want Redis
        blips to create a self-inflicted DoS."""
        from app.services.portal import password_reset_service

        class BoomRedis:
            def incr(self, _key):
                raise RuntimeError("redis down")

            def expire(self, _key, _ttl):
                return None

            def ttl(self, _key):
                return 0

        # Should not raise.
        password_reset_service._rate_limit_check_and_bump(
            BoomRedis(), email_norm="fail-open@example.com"
        )


# ---------------------------------------------------------------------------
# Full integration — request → email sent → confirm → login with new pwd
# ---------------------------------------------------------------------------


class TestFullFlowIntegration:
    @pytest.fixture()
    def client(self, redis_client, monkeypatch):
        from app.api.routes import portal_auth

        def _fake_redis_dep():
            return redis_client

        app.dependency_overrides[portal_auth._portal_redis_client] = _fake_redis_dep
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(
                portal_auth._portal_redis_client, None
            )

    def test_end_to_end_user_can_log_in_with_new_password(self, client):
        _seed_user(email="e2e@example.com", password="OldPass1234")

        # 1. Request reset
        issued = client.post(
            "/portal/auth/password-reset/request",
            json={"email": "e2e@example.com"},
        ).json()
        token = issued["token"]

        # 2. Confirm
        r = client.post(
            "/portal/auth/password-reset/confirm",
            json={"token": token, "new_password": "ShinyNew1234"},
        )
        assert r.status_code == 200

        # 3. Old password fails
        r_old = client.post(
            "/portal/auth/login",
            json={"email": "e2e@example.com", "password": "OldPass1234"},
        )
        assert r_old.status_code == 401

        # 4. New password works
        r_new = client.post(
            "/portal/auth/login",
            json={"email": "e2e@example.com", "password": "ShinyNew1234"},
        )
        assert r_new.status_code == 200
        # HttpOnly migration: login issues the session as cookies, not body.
        assert r_new.cookies.get("portal_access_token")
