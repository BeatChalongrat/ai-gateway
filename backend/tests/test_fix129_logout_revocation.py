"""FIX129 B2 — sign-out ends the session now.

Before: logout blacklisted the refresh token only; the access token kept
answering /portal/me (and every chat-edge route) for its remaining 60 min.
Now the access token's jti is revoked in Redis, ``get_portal_user`` refuses
it, and chat-edge is told through its internal revoke endpoint.
"""

from __future__ import annotations

import uuid

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.routes import portal_auth as portal_auth_routes
from app.core.config import settings
from app.main import app
from app.models.portal_user import PortalUserORM
from app.services.portal import auth_service, chat_edge_revoke
from tests.conftest import _integration_engine

_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture()
def redis_client(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth_service, "_revocation_redis_client", r)
    app.dependency_overrides[portal_auth_routes._portal_redis_client] = lambda: r
    try:
        yield r
    finally:
        app.dependency_overrides.pop(portal_auth_routes._portal_redis_client, None)


@pytest.fixture()
def user():
    uid = str(uuid.uuid4())
    db = _Session()
    try:
        u = PortalUserORM(
            id=uid, email=f"fix129-logout-{uid[:8]}@test.local", password_hash="x",
            credit_balance=10, credit_used=0, credit_quota=10,
            tenant_id="test-tenant", tenant_role="member", token_version=1,
        )
        db.add(u)
        db.commit()
    finally:
        db.close()
    return uid


def _token(uid: str) -> str:
    return auth_service.create_portal_token(
        uid, "x@test.local", "access", tenant_id="test-tenant", tenant_role="member", token_version=1
    )


def test_logout_revokes_the_access_token_everywhere(redis_client, user, monkeypatch):
    told: list[dict] = []
    monkeypatch.setattr(
        chat_edge_revoke, "revoke_on_chat_edge",
        lambda **kw: (told.append(kw) or True),
    )
    client = TestClient(app)
    tok = _token(user)
    hdr = {"Authorization": f"Bearer {tok}"}

    assert client.get("/portal/me", headers=hdr).status_code == 200

    r = client.post("/portal/auth/logout", headers=hdr)
    assert r.status_code == 200, r.text

    # The same token is refused from now on — this is the line that was red.
    r = client.get("/portal/me", headers=hdr)
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "token_revoked"

    # …and chat-edge was told which jti died.
    import jwt as _jwt

    jti = _jwt.decode(tok, options={"verify_signature": False})["jti"]
    assert told and told[0]["jti"] == jti and told[0]["sub"] == user
    assert redis_client.ttl(auth_service._ACCESS_REVOKED_PREFIX + jti) > 0


def test_other_tokens_of_the_user_keep_working(redis_client, user, monkeypatch):
    monkeypatch.setattr(chat_edge_revoke, "revoke_on_chat_edge", lambda **kw: True)
    client = TestClient(app)
    a, b = _token(user), _token(user)
    client.post("/portal/auth/logout", headers={"Authorization": f"Bearer {a}"})
    assert client.get("/portal/me", headers={"Authorization": f"Bearer {a}"}).status_code == 401
    assert client.get("/portal/me", headers={"Authorization": f"Bearer {b}"}).status_code == 200


def test_redis_outage_fails_open(user, monkeypatch):
    class _Down:
        def exists(self, *_):
            raise ConnectionError("redis down")

    monkeypatch.setattr(auth_service, "_revocation_redis_client", _Down())
    assert auth_service.is_access_token_revoked("some-jti") is False


class TestChatEdgeRevoke:
    def test_unconfigured_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(settings, "chat_edge_internal_url", "")
        assert chat_edge_revoke.revoke_on_chat_edge(jti="j", exp=1, sub="s") is False

    def test_posts_with_the_internal_secret(self, monkeypatch):
        monkeypatch.setattr(settings, "chat_edge_internal_url", "http://chat-edge-api:4000/")
        monkeypatch.setattr(settings, "internal_service_secret", "s3cret")
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(204)

        real = httpx.Client
        monkeypatch.setattr(
            chat_edge_revoke.httpx, "Client",
            lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        )
        assert chat_edge_revoke.revoke_on_chat_edge(jti="abc", exp=1_900_000_000, sub="u1") is True
        assert str(seen[0].url) == "http://chat-edge-api:4000/internal/auth/revoke"
        assert seen[0].headers["X-Internal-Service-Secret"] == "s3cret"
        assert b'"jti": "abc"' in seen[0].content or b'"jti":"abc"' in seen[0].content

    def test_wrong_secret_does_not_retry(self, monkeypatch):
        monkeypatch.setattr(settings, "chat_edge_internal_url", "http://chat-edge-api:4000")
        monkeypatch.setattr(settings, "internal_service_secret", "bad")
        calls = []
        real = httpx.Client
        monkeypatch.setattr(
            chat_edge_revoke.httpx, "Client",
            lambda **kw: real(transport=httpx.MockTransport(lambda r: (calls.append(r) or httpx.Response(401))), **kw),
        )
        assert chat_edge_revoke.revoke_on_chat_edge(jti="abc", exp=1, sub=None) is False
        assert len(calls) == 1
