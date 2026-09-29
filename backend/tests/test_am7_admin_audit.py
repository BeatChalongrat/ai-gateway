"""AM7 — Agent Market's admin changes land in the admin audit log.

Agent Market's settings live in chat-edge's database; the admin audit log
auditors read (admin-ui /audit-log, table ``admin_audit_log``) lives in the
gateway's. chat-edge writes each admin change — who, which setting, from what
to what — through ``POST /internal/admin-audit``, as the trusted chat-edge
service (a short-lived service JWT). These tests pin that door:

* a chat-edge service token writes the entries, all of them or none;
* only ``agent_market.*`` actions, only ``session:`` / ``chat-user:`` actors —
  the door cannot forge any other line of the log;
* anything else (no token, an API key, a token signed with another secret) is
  refused and writes nothing;
* the entry is found by the setting's name in the endpoint the page searches,
  with its old and new value.

And the scoped role (phase 2): an ``agent_publisher`` admin-ui account holding
a legacy admin JWT resolves to a viewer, never to an admin.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.main import app
from app.models.admin_audit_log import AdminAuditLogORM
from tests.conftest import _integration_engine

_Session = sessionmaker(bind=_integration_engine)
_SECRET = "am7-service-secret-for-tests-0123456789abcdef"


def _mint(*, secret: str = _SECRET, iss: str = "chat-edge-api", aud: str = "gateway-api") -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode(
        {"iss": iss, "aud": aud, "iat": now, "exp": now + dt.timedelta(seconds=60)},
        secret,
        algorithm="HS256",
    )


def _headers(token: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token or _mint()}",
        "X-User-ID": "agent-market-audit",
        "X-Tenant-ID": "__global__",
    }


EGRESS = {
    "actor_key_id": "session:0b8f5a8e-0000-4000-8000-000000000001",
    "tenant_id": "__global__",
    "action": "agent_market.org.documents_may_leave",
    "resource_type": "agent_market_org",
    "resource_id": "default",
    "details": {
        "setting": "documents_may_leave",
        "label": "เอกสารส่งไปโมเดลนอกองค์กรได้",
        "from": False,
        "to": True,
        "by": "it-admin@example.test",
        "via": "admin-ui",
        "org": "default",
    },
    "outcome": "success",
}


@pytest.fixture(autouse=True)
def _service_secret(monkeypatch):
    monkeypatch.setattr(settings, "service_jwt_secret", _SECRET, raising=False)
    db = _Session()
    try:
        db.query(AdminAuditLogORM).filter(AdminAuditLogORM.action.like("agent_market.%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _rows() -> list[AdminAuditLogORM]:
    db = _Session()
    try:
        rows = db.query(AdminAuditLogORM).filter(AdminAuditLogORM.action.like("agent_market.%")).order_by(AdminAuditLogORM.id).all()
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def test_chat_edge_writes_the_egress_change_with_its_old_and_new_value(client):
    r = client.post("/internal/admin-audit", json={"entries": [EGRESS]}, headers=_headers())
    assert r.status_code == 201, r.text
    assert r.json()["recorded"] == 1
    [row] = _rows()
    assert row.actor_key_id == EGRESS["actor_key_id"]
    assert row.action == "agent_market.org.documents_may_leave"
    assert row.resource_type == "agent_market_org"
    assert row.resource_id == "default"
    assert row.outcome == "success"
    details = json.loads(row.details)
    assert details["from"] is False and details["to"] is True
    assert details["by"] == "it-admin@example.test"
    assert details["label"] == "เอกสารส่งไปโมเดลนอกองค์กรได้"


def test_the_page_finds_it_by_the_setting_name(client):
    assert client.post("/internal/admin-audit", json={"entries": [EGRESS]}, headers=_headers()).status_code == 201
    r = client.get("/admin/audit-log?q=documents_may_leave", headers={"Authorization": "Bearer gw_test_"})
    assert r.status_code == 200, r.text
    items = [i for i in r.json()["items"] if i["action"].startswith("agent_market.")]
    assert len(items) == 1
    assert items[0]["action"] == "agent_market.org.documents_may_leave"
    got = items[0]["details"]
    got = json.loads(got) if isinstance(got, str) else got
    assert got["from"] is False and got["to"] is True


def test_a_batch_is_written_whole_or_not_at_all(client):
    member = dict(EGRESS, action="agent_market.member.added", resource_type="agent_market_member",
                  details={"email": "somchai@example.test", "to": {"role": "member"}})
    assert client.post("/internal/admin-audit", json={"entries": [EGRESS, member]}, headers=_headers()).status_code == 201
    assert [r.action for r in _rows()] == ["agent_market.org.documents_may_leave", "agent_market.member.added"]

    bad = dict(EGRESS, resource_id="x" * 37)
    r = client.post("/internal/admin-audit", json={"entries": [EGRESS, bad]}, headers=_headers())
    assert r.status_code == 422
    assert len(_rows()) == 2  # nothing of the refused batch


@pytest.mark.parametrize(
    "override",
    [
        {"action": "key_create"},  # not Agent Market's to write
        {"action": "fallback.kill_switch"},
        {"actor_key_id": "system:retention-pipeline"},  # not an admin or a chat user
        {"actor_key_id": "jwt:someone"},
        {"outcome": "maybe"},
        {"tenant_id": "t" * 37},
    ],
)
def test_the_door_cannot_forge_another_line_of_the_log(client, override):
    r = client.post("/internal/admin-audit", json={"entries": [dict(EGRESS, **override)]}, headers=_headers())
    assert r.status_code == 422, r.text
    assert _rows() == []


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no token
        {"Authorization": "Bearer gw_test_"},  # an API key, even an admin one
        {"Authorization": f"Bearer {_mint(secret='another-secret-0123456789abcdef0123')}", "X-User-ID": "x", "X-Tenant-ID": "y"},
        {"Authorization": f"Bearer {_mint(iss='someone-else')}", "X-User-ID": "x", "X-Tenant-ID": "y"},
    ],
)
def test_only_chat_edge_may_write(client, headers):
    r = client.post("/internal/admin-audit", json={"entries": [EGRESS]}, headers=headers)
    assert r.status_code in (401, 403), r.text
    assert _rows() == []


def test_a_legacy_jwt_of_an_agent_publisher_is_a_viewer_not_an_admin():
    from app.api.deps import _resolve_jwt_token
    from app.api.routes.auth import _create_access_token
    from app.models.admin_user import AdminUserORM

    db = _Session()
    try:
        user = AdminUserORM(
            id=str(uuid.uuid4()),
            username=f"am7-publisher-{uuid.uuid4().hex[:6]}",
            email=f"am7-publisher-{uuid.uuid4().hex[:6]}@example.test",
            hashed_password="x",
            role="agent_publisher",
            is_active=True,
        )
        db.add(user)
        db.commit()
        token, _ = _create_access_token(user)
        resolved = _resolve_jwt_token(token, db)
        assert resolved.role == "viewer"
        db.delete(user)
        db.commit()
    finally:
        db.close()
