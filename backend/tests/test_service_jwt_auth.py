"""FIX 1 — chat-path decoupling from gw_ keys via a service JWT (Option B).

The gateway now accepts a chat-edge SERVICE JWT (iss=chat-edge-api,
aud=gateway-api, signed with service_jwt_secret) and resolves the END USER from
the trusted X-User-ID / X-Tenant-ID headers. These tests pin the security
properties: valid path resolves the user, and it is FAIL-CLOSED.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest

from app.api.deps import _resolve_service_jwt, get_resolved_api_key
from app.core.config import settings
from app.core.exceptions import AuthenticationError

_SECRET = "svc-secret-for-tests-0123456789abcdef"


def _mint(
    *,
    secret: str = _SECRET,
    iss: str = "chat-edge-api",
    aud: str = "gateway-api",
    ttl_seconds: int = 60,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + dt.timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def _enable_service_jwt(monkeypatch):
    monkeypatch.setattr(settings, "service_jwt_secret", _SECRET, raising=False)
    yield


# ---------------------------------------------------------------------------
# _resolve_service_jwt — the core logic
# ---------------------------------------------------------------------------


def test_valid_service_jwt_resolves_the_end_user():
    resolved = _resolve_service_jwt(
        _mint(), x_user_id="user-123", x_tenant_id="tenant-abc",
        trusted_conversation_id="conv-9",
    )
    assert resolved is not None
    assert resolved.portal_user_id == "user-123"       # billed to the end user
    assert resolved.tenant_id == "tenant-abc"
    assert resolved.internal_trusted is True
    assert resolved.rate_limit_key_id == "svc:user-123"  # per-user bucket
    assert resolved.role == "user"
    assert resolved.trusted_conversation_id == "conv-9"


def test_fail_closed_when_user_identity_missing():
    # A valid service token with NO on-behalf-of user must be rejected, never
    # downgraded to anonymous.
    with pytest.raises(AuthenticationError):
        _resolve_service_jwt(_mint(), x_user_id=None, x_tenant_id="t1",
                             trusted_conversation_id=None)
    with pytest.raises(AuthenticationError):
        _resolve_service_jwt(_mint(), x_user_id="  ", x_tenant_id="t1",
                             trusted_conversation_id=None)


def test_fail_closed_when_tenant_identity_missing():
    with pytest.raises(AuthenticationError):
        _resolve_service_jwt(_mint(), x_user_id="u1", x_tenant_id=None,
                             trusted_conversation_id=None)


@pytest.mark.parametrize(
    "token",
    [
        _mint(secret="the-wrong-secret-xxxxxxxxxxxxxxxxxxxx"),  # bad signature
        _mint(iss="somebody-else"),                            # wrong issuer
        _mint(aud="not-the-gateway"),                          # wrong audience
        _mint(ttl_seconds=-10),                                # expired
        "not-even-a-jwt",
    ],
)
def test_non_service_tokens_fall_through_as_none(token):
    # Not our service token → None so the caller can try the admin path; the
    # outer resolver 401s if nothing validates (fail-closed overall).
    assert _resolve_service_jwt(token, x_user_id="u1", x_tenant_id="t1",
                                trusted_conversation_id=None) is None


def test_feature_off_when_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "service_jwt_secret", "", raising=False)
    assert _resolve_service_jwt(_mint(), x_user_id="u1", x_tenant_id="t1",
                                trusted_conversation_id=None) is None


# ---------------------------------------------------------------------------
# get_resolved_api_key — the wired dependency
# ---------------------------------------------------------------------------


def test_get_resolved_api_key_accepts_service_jwt():
    resolved = get_resolved_api_key(
        authorization="Bearer " + _mint(),
        x_user_id="user-777",
        x_tenant_id="tenant-777",
        x_internal_service_secret=None,
        x_conversation_id="conv-1",
    )
    assert resolved.portal_user_id == "user-777"
    assert resolved.tenant_id == "tenant-777"
    assert resolved.internal_trusted is True


def test_missing_bearer_still_rejected():
    with pytest.raises(AuthenticationError):
        get_resolved_api_key(authorization=None)
    with pytest.raises(AuthenticationError):
        get_resolved_api_key(authorization="Bearer ")
