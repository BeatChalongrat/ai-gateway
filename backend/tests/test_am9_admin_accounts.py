"""AM9 (E): admin-ui accounts and their roles, granted on a screen instead of by SQL.

Before AM9 the backend had no route for this at all (``/admin/accounts`` answered 404): giving the finance
lead ``agent_publisher`` meant ``update admin_users set role=…``. Now an admin lists, creates, changes and
deactivates accounts; every change is in the admin audit log; nobody lowers their own role or deactivates
themselves; the last active admin stays; a changed account's sessions end.
"""

from __future__ import annotations

import json
import uuid

import bcrypt
import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.services.admin.session_manager import SessionManager

PW = "CorrectHorseBattery9!"


@pytest.fixture
def session_manager():
    return SessionManager(redis_client=fakeredis.FakeRedis(decode_responses=True), signing_secrets=["s" * 48],
                          idle_ttl_seconds=3600, absolute_ttl_seconds=86400)


def _user(db, email, role, active=True):
    from app.models.admin_user import AdminUserORM

    u = AdminUserORM(id=str(uuid.uuid4()), username=email, email=email, role=role, is_active=active,
                     hashed_password=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(rounds=4)).decode())
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def people(db_session):
    return {"admin": _user(db_session, "it@example.test", "admin"),
            "lead": _user(db_session, "lead@example.test", "viewer"),
            "pub": _user(db_session, "pub@example.test", "agent_publisher")}


@pytest.fixture
def app_for(sqlite_engine, session_manager):
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker

    from app.api.deps import get_db_session
    from app.api.routes.admin_accounts import router
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import handle_gateway_error
    from app.services.admin.session_service import get_session_manager

    app = FastAPI()
    app.add_exception_handler(GatewayError, handle_gateway_error)
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

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_session_manager] = lambda: session_manager
    app.include_router(router)

    def as_(user):
        cookie, _ = session_manager.create_session(user_id=user.id, tenant_id=None, role=user.role, ip="t", user_agent="t")
        c = TestClient(app)
        c.cookies.set("admin_session", cookie)
        return c
    return as_


def _audit(db, action):
    from app.models.admin_audit_log import AdminAuditLogORM

    db.expire_all()
    return [(r.actor_key_id, r.resource_id, json.loads(r.details or "{}")) for r in
            db.query(AdminAuditLogORM).filter(AdminAuditLogORM.action == action).all()]


def test_an_admin_grants_agent_publisher_on_the_screen_and_the_log_keeps_it(app_for, people, db_session, session_manager):
    lead = people["lead"]
    session_manager.create_session(user_id=lead.id, tenant_id=None, role="viewer", ip="t", user_agent="t")
    c = app_for(people["admin"])
    listed = c.get("/admin/accounts").json()
    assert {a["email"] for a in listed["accounts"]} == {"it@example.test", "lead@example.test", "pub@example.test"}
    assert "agent_publisher" in listed["roles"]
    r = c.patch(f"/admin/accounts/{lead.id}", json={"role": "agent_publisher"})
    assert r.status_code == 200, r.text
    assert r.json()["account"]["role"] == "agent_publisher"
    assert r.json()["sessions_ended"] == 1          # the old role does not linger in an open session
    [(actor, rid, d)] = _audit(db_session, "admin_account.role_changed")
    assert (actor, rid, d["from"], d["to"], d["email"]) == (f"session:{people['admin'].id}", lead.id, "viewer", "agent_publisher",
                                                            "lead@example.test")


def test_a_new_account_is_created_with_its_role_and_never_logs_its_password(app_for, people, db_session):
    c = app_for(people["admin"])
    r = c.post("/admin/accounts", json={"email": "Finance@Example.test", "role": "agent_publisher", "password": "a-long-passphrase-1"})
    assert r.status_code == 201, r.text
    assert r.json()["account"]["email"] == "finance@example.test"
    [(_, _, d)] = _audit(db_session, "admin_account.created")
    assert d["role"] == "agent_publisher" and "a-long-passphrase-1" not in json.dumps(d)
    assert c.post("/admin/accounts", json={"email": "x@example.test", "role": "agent_publisher", "password": "short"}).status_code == 422
    assert c.post("/admin/accounts", json={"email": "x@example.test", "role": "owner", "password": "a-long-passphrase-1"}).status_code == 422
    assert c.post("/admin/accounts", json={"email": "finance@example.test", "role": "viewer", "password": "a-long-passphrase-1"}).status_code == 422


def test_only_an_admin_may_manage_accounts(app_for, people):
    for who in ("pub", "lead"):
        c = app_for(people[who])
        assert c.get("/admin/accounts").status_code == 403
        assert c.patch(f"/admin/accounts/{people[who].id}", json={"role": "admin"}).status_code == 403


def test_nobody_lowers_their_own_role_and_the_last_admin_stays(app_for, people):
    me = people["admin"]
    c = app_for(me)
    r = c.patch(f"/admin/accounts/{me.id}", json={"role": "viewer"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "own_role"
    r = c.patch(f"/admin/accounts/{me.id}", json={"is_active": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "own_account"
    # a second admin may not demote the only other one either, once it is the last
    second = c.post("/admin/accounts", json={"email": "ops@example.test", "role": "admin", "password": "a-long-passphrase-2"}).json()["account"]
    assert c.patch(f"/admin/accounts/{second['id']}", json={"is_active": False}).status_code == 200
    c2 = app_for(people["admin"])
    assert c2.patch(f"/admin/accounts/{second['id']}", json={"is_active": True}).status_code == 200


def test_a_deactivated_account_cannot_sign_in_and_its_sessions_end(app_for, people, session_manager, db_session):
    pub = people["pub"]
    cookie, _ = session_manager.create_session(user_id=pub.id, tenant_id=None, role=pub.role, ip="t", user_agent="t")
    c = app_for(people["admin"])
    r = c.patch(f"/admin/accounts/{pub.id}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["sessions_ended"] >= 1
    with pytest.raises(Exception):
        session_manager.verify_cookie(cookie)
    assert _audit(db_session, "admin_account.deactivated")
