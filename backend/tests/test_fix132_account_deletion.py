"""FIX132 C — a user deletes their own account.

Request (POST /portal/me/delete-account):
- the account's own email must be typed (422 confirm_mismatch otherwise);
- the only owner of a team that still has members is refused, in Thai (409);
- chat-edge must confirm first — when it cannot, nothing changes (503);
- on success: status pending_deletion, deletion_requested_at set, every
  session ended (token_version), API keys revoked, sign-in refused
  (the OAuth door: test_fix132_oauth_inactive.py), cookies expired.

Purge (the nightly pipeline's step 5):
- 30 days after the request chat-edge is asked to purge, then the portal
  row becomes an anonymised tombstone; connector tokens, generation jobs
  and request-log text go; the credit ledger stays;
- inside the window, when chat-edge fails, or with the purge off / in
  dry-run, nothing is erased.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.connector_token import ConnectorTokenORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.generation_job import GenerationJobORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.request_log import RequestLogORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from tests.conftest import _integration_engine

_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(TenantInviteORM).delete(synchronize_session=False)
        db.query(ConnectorTokenORM).delete(synchronize_session=False)
        db.query(GenerationJobORM).delete(synchronize_session=False)
        db.query(RequestLogORM).filter(RequestLogORM.request_id.like("fix132-%")).delete(
            synchronize_session=False
        )
        db.query(ApiKeyORM).filter(ApiKeyORM.portal_user_id.isnot(None)).delete(
            synchronize_session=False
        )
        db.query(ProjectORM).filter(ProjectORM.portal_user_id.isnot(None)).delete(
            synchronize_session=False
        )
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.query(TenantORM).filter(~TenantORM.tenant_id.in_(["test-tenant"])).delete(
            synchronize_session=False
        )
        db.query(SystemSettingORM).delete(synchronize_session=False)
        db.add_all(
            [
                SystemSettingORM(key="free_trial_tokens", value="1000"),
                SystemSettingORM(key="portal_signup_enabled", value="true"),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


class _FakeChatEdge:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requested: list[str] = []
        self.purged: list[str] = []

    def request_deletion(self, user_id):
        self.requested.append(user_id)
        return None if self.fail else {"conversations_hidden": 2}

    def purge(self, user_id):
        self.purged.append(user_id)
        return None if self.fail else {"purged": True, "counts": {"conversations": 2}}


@pytest.fixture
def chat_edge(monkeypatch):
    fake = _FakeChatEdge()
    import app.api.routes.portal_me as portal_me

    monkeypatch.setattr(portal_me, "_chat_edge_account_client", lambda: fake)
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client, email, **kw):
    r = client.post("/portal/auth/signup", json={"email": email, "password": "Test1234!", **kw})
    assert r.status_code == 200, r.text
    body = r.json()
    body["access_token"] = r.cookies.get("portal_access_token")
    body["refresh_token"] = r.cookies.get("portal_refresh_token")
    client.cookies.clear()
    return body


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _user(email) -> PortalUserORM:
    db = _Session()
    try:
        u = db.query(PortalUserORM).filter(PortalUserORM.email == email).one()
        db.expunge(u)
        return u
    finally:
        db.close()


def _verify(email):
    db = _Session()
    db.query(PortalUserORM).filter(PortalUserORM.email == email).update({"email_verified": True})
    db.commit()
    db.close()


def test_the_typed_email_must_match(client, chat_edge):
    me = _signup(client, "alice@t.com")
    r = client.post("/portal/me/delete-account", headers=_auth(me["access_token"]), json={"confirm": "alice@t.co"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "confirm_mismatch"
    assert "อีเมล" in r.json()["error"]["message"]
    assert _user("alice@t.com").status == "active"
    assert chat_edge.requested == []


def test_the_only_owner_of_a_team_with_members_is_refused_in_thai(client, chat_edge):
    owner = _signup(client, "owner@t.com")
    inv = client.post("/portal/team/invites", headers=_auth(owner["access_token"]), json={}).json()
    _signup(client, "member@t.com", invite_code=inv["code"])

    r = client.post(
        "/portal/me/delete-account", headers=_auth(owner["access_token"]), json={"confirm": "owner@t.com"}
    )
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "sole_owner_with_members"
    assert "เจ้าของทีมเพียงคนเดียว" in err["message"] and "1 คน" in err["message"]
    assert _user("owner@t.com").status == "active"
    assert chat_edge.requested == []


def test_a_member_of_that_team_may_leave_by_deleting(client, chat_edge):
    owner = _signup(client, "owner@t.com")
    inv = client.post("/portal/team/invites", headers=_auth(owner["access_token"]), json={}).json()
    member = _signup(client, "member@t.com", invite_code=inv["code"])
    r = client.post(
        "/portal/me/delete-account", headers=_auth(member["access_token"]), json={"confirm": "member@t.com"}
    )
    assert r.status_code == 200, r.text


def test_when_chat_edge_cannot_confirm_nothing_changes(client, monkeypatch):
    fake = _FakeChatEdge(fail=True)
    import app.api.routes.portal_me as portal_me

    monkeypatch.setattr(portal_me, "_chat_edge_account_client", lambda: fake)
    me = _signup(client, "alice@t.com")
    r = client.post("/portal/me/delete-account", headers=_auth(me["access_token"]), json={"confirm": "alice@t.com"})
    assert r.status_code == 503, r.text
    assert "ยังไม่มีข้อมูลใดถูกลบ" in r.json()["error"]["message"]
    u = _user("alice@t.com")
    assert u.status == "active" and u.deletion_requested_at is None


def test_deleting_ends_every_session_and_refuses_sign_in(client, chat_edge):
    me = _signup(client, "alice@t.com")
    _verify("alice@t.com")
    uid = _user("alice@t.com").id
    db = _Session()
    db.add(
        ApiKeyORM(
            key_id=str(uuid.uuid4()), key_prefix="gw_fix132", key_hash=uuid.uuid4().hex,
            tenant_id="test-tenant", project_id="test-project", name="mine",
            portal_user_id=uid, status="active",
        )
    )
    db.commit()
    db.close()

    r = client.post(
        "/portal/me/delete-account", headers=_auth(me["access_token"]), json={"confirm": " Alice@T.com "}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_deletion" and body["purge_days"] == 30
    assert "portal_access_token" in r.headers.get("set-cookie", "")  # expired on this device
    assert chat_edge.requested == [uid]

    u = _user("alice@t.com")
    assert u.status == "pending_deletion" and u.deletion_requested_at is not None
    db = _Session()
    assert db.query(ApiKeyORM).filter(ApiKeyORM.portal_user_id == uid).one().status == "revoked"
    db.close()

    # The old token is dead; sign-in and refresh are refused.
    assert client.get("/portal/me", headers=_auth(me["access_token"])).status_code in (401, 403)
    r = client.post("/portal/auth/login", json={"email": "alice@t.com", "password": "Test1234!"})
    assert r.status_code == 401
    client.cookies.set("portal_refresh_token", me["refresh_token"])
    assert client.post("/portal/auth/refresh").status_code == 401
    client.cookies.clear()


# ---------------------------------------------------------------------------
# The nightly purge
# ---------------------------------------------------------------------------


def _pending_user(email, days_ago, *, with_data=True) -> str:
    uid = str(uuid.uuid4())
    tenant = f"t-{uid[:8]}"
    local = email.split("@")[0]
    db = _Session()
    db.add(TenantORM(tenant_id=tenant, name=f"{local.capitalize()}'s Team", slug=tenant))
    db.flush()
    db.add(
        PortalUserORM(
            id=uid, email=email, password_hash="x", display_name="Alice A.", status="pending_deletion",
            email_verified=True, oauth_provider="google", oauth_id=f"g-{uid}", avatar_url="/static/avatars/a.webp",
            tenant_id=tenant, tenant_role="owner", token_version=2,
            deletion_requested_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
    )
    db.flush()
    if with_data:
        db.add(ConnectorTokenORM(id=str(uuid.uuid4()), portal_user_id=uid, connector_id="gh",
                                 provider="github", access_token_encrypted="enc", scopes="repo"))
        db.add(GenerationJobORM(id=str(uuid.uuid4()), tenant_id=tenant, user_id=uid, job_type="video",
                                provider="mock", model_id="m", status="succeeded", input_prompt="วิดีโอลับ",
                                input_metadata={}, estimated_cost_credits=1,
                                ttl_expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
        db.add(RequestLogORM(request_id=f"fix132-{uid}", tenant_id=tenant, project_id="p", provider="openai",
                             model="gpt", status="success", policy_action="allow", latency_ms=1,
                             prompt_tokens=10, user_id=uid, input_raw="ความลับ", input_masked="[x]",
                             output_final="คำตอบ"))
        db.add(CreditTransactionORM(id=str(uuid.uuid4()), user_id=uid, amount=-5, balance_after=95,
                                    type="usage", request_id=f"fix132-{uid}"))
    db.commit()
    db.close()
    return uid


def _purge(chat_edge, mode="on"):
    from app.services.portal.account_deletion import purge_due_accounts

    return purge_due_accounts(_Session, chat_edge=chat_edge, mode=mode, days=30)


def test_purge_after_30_days_leaves_an_anonymised_tombstone():
    due = _pending_user("alice@t.com", 31)
    recent = _pending_user("bob@t.com", 29)
    fake = _FakeChatEdge()

    res = _purge(fake)
    assert res.purged == 1 and fake.purged == [due]

    db = _Session()
    u = db.get(PortalUserORM, due)
    assert u.status == "deleted" and u.deleted_at is not None
    assert u.email == f"deleted-{due}@deleted.invalid"
    assert u.display_name is None and u.avatar_url is None and u.oauth_id is None and u.oauth_provider is None
    assert db.get(TenantORM, u.tenant_id).name == "Deleted account"
    assert db.query(ConnectorTokenORM).filter_by(portal_user_id=due).count() == 0
    assert db.query(GenerationJobORM).filter_by(user_id=due).count() == 0
    log = db.get(RequestLogORM, f"fix132-{due}")
    assert log.user_id is None and log.input_raw is None and log.input_masked is None and log.output_final is None
    assert log.prompt_tokens == 10  # the usage numbers stay
    tx = db.query(CreditTransactionORM).filter_by(user_id=due).one()
    assert tx.amount == -5  # financial record kept, pointing at the tombstone

    # Inside the window: untouched.
    b = db.get(PortalUserORM, recent)
    assert b.status == "pending_deletion" and b.email == "bob@t.com"
    assert db.query(ConnectorTokenORM).filter_by(portal_user_id=recent).count() == 1
    db.close()

    # The freed email can sign up again.
    c = TestClient(app)
    assert c.post("/portal/auth/signup", json={"email": "alice@t.com", "password": "Test1234!"}).status_code == 200


def test_purge_waits_when_chat_edge_does_not_confirm():
    due = _pending_user("alice@t.com", 40)
    res = _purge(_FakeChatEdge(fail=True))
    assert res.purged == 0 and res.failed == 1
    db = _Session()
    assert db.get(PortalUserORM, due).status == "pending_deletion"
    assert db.query(ConnectorTokenORM).filter_by(portal_user_id=due).count() == 1
    db.close()


@pytest.mark.parametrize("mode", ["off", "dry_run"])
def test_purge_off_or_dry_run_erases_nothing(mode):
    due = _pending_user("alice@t.com", 40)
    fake = _FakeChatEdge()
    res = _purge(fake, mode=mode)
    assert res.mode == mode and res.purged == 0 and fake.purged == []
    assert res.due == (1 if mode == "dry_run" else 0)
    db = _Session()
    assert db.get(PortalUserORM, due).email == "alice@t.com"
    db.close()
