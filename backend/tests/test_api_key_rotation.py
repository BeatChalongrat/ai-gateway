"""Phase 4 Feature 2 — API key rotation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from app.services.api_key_service import ApiKeyService
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)
_svc = ApiKeyService()


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(TenantInviteORM).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(
            ApiKeyORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.query(TenantORM).filter(
            ~TenantORM.tenant_id.in_(["test-tenant"])
        ).delete(synchronize_session=False)
        db.query(SystemSettingORM).delete(synchronize_session=False)
        db.add_all(
            [
                SystemSettingORM(key="free_trial_tokens", value="100000"),
                SystemSettingORM(key="portal_signup_enabled", value="true"),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup_and_key(client) -> dict:
    sr = client.post(
        "/portal/auth/signup",
        json={"email": "rot@example.com", "password": "Test1234!"},
    )
    signup = sr.json()
    # HttpOnly migration: the access token comes back as a cookie now.
    token = sr.cookies.get("portal_access_token")
    proj = client.post(
        "/portal/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Rot Proj"},
    ).json()
    key = client.post(
        f"/portal/projects/{proj['id']}/keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "original"},
    ).json()
    return {
        "token": token,
        "user_id": signup["user"]["id"],
        "project_id": proj["id"],
        "key_id": key["key_id"],
        "original_api_key": key["api_key"],
        "original_key_prefix": key["key_prefix"],
    }


class TestRotateKeyService:
    def test_rotate_issues_new_key_value_and_grace_expires_old(self):
        db = _Session()
        try:
            raw_old, old_orm = _svc.create_key(
                db,
                tenant_id="test-tenant",
                project_id="test-project",
                name="orig",
            )
            old_key_id = old_orm.key_id
            db.commit()
            result = _svc.rotate_key(db, old_key_id)
            assert result is not None
            raw_new, new_orm = result
            new_key_id = new_orm.key_id
            new_name = new_orm.name
            new_project = new_orm.project_id
            db.commit()
        finally:
            db.close()
        assert raw_new != raw_old
        assert new_key_id != old_key_id
        assert new_name == "orig"
        assert new_project == "test-project"

        # Old key now has a grace expiry within the next hour.
        db = _Session()
        try:
            refreshed_old = db.get(ApiKeyORM, old_key_id)
            assert refreshed_old.expires_at is not None
            exp = refreshed_old.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            delta = exp - datetime.now(timezone.utc)
            assert timedelta(minutes=55) < delta < timedelta(minutes=61)
        finally:
            db.close()

    def test_old_key_still_verifies_during_grace_period(self):
        db = _Session()
        try:
            raw_old, old_orm = _svc.create_key(
                db,
                tenant_id="test-tenant",
                project_id="test-project",
                name="orig",
            )
            _svc.rotate_key(db, old_orm.key_id)
            db.commit()
            # Old key still resolves (expiry 1h out).
            verified = _svc.verify_key(db, raw_old)
        finally:
            db.close()
        assert verified is not None

    def test_new_key_verifies_immediately(self):
        db = _Session()
        try:
            _, old_orm = _svc.create_key(
                db,
                tenant_id="test-tenant",
                project_id="test-project",
                name="orig",
            )
            raw_new, new_orm = _svc.rotate_key(db, old_orm.key_id)
            db.commit()
            verified = _svc.verify_key(db, raw_new)
        finally:
            db.close()
        assert verified is not None
        assert verified.key_id == new_orm.key_id

    def test_rotating_revoked_key_returns_none(self):
        db = _Session()
        try:
            _, old_orm = _svc.create_key(
                db,
                tenant_id="test-tenant",
                project_id="test-project",
                name="orig",
            )
            _svc.revoke_key(db, old_orm.key_id)
            result = _svc.rotate_key(db, old_orm.key_id)
        finally:
            db.close()
        assert result is None

    def test_rotating_missing_key_returns_none(self):
        db = _Session()
        try:
            result = _svc.rotate_key(db, "nonexistent")
        finally:
            db.close()
        assert result is None

    def test_rotation_preserves_shorter_existing_expiry(self):
        """A deliberate short-TTL key should not have its expiry
        pushed out to 1h by rotation."""
        db = _Session()
        try:
            _, old_orm = _svc.create_key(
                db,
                tenant_id="test-tenant",
                project_id="test-project",
                name="short-lived",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            _svc.rotate_key(db, old_orm.key_id)
            db.commit()
            refreshed = db.get(ApiKeyORM, old_orm.key_id)
            exp = refreshed.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            delta = exp - datetime.now(timezone.utc)
            assert delta < timedelta(minutes=15)
        finally:
            db.close()


class TestRotateKeyEndpoint:
    def test_rotate_via_api_returns_new_key_value(self, client):
        ctx = _signup_and_key(client)
        r = client.post(
            f"/portal/projects/{ctx['project_id']}/keys/{ctx['key_id']}/rotate",
            headers={"Authorization": f"Bearer {ctx['token']}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["old_key_id"] == ctx["key_id"]
        assert body["key_id"] != ctx["key_id"]
        assert body["api_key"] != ctx["original_api_key"]
        assert body["name"] == "original"
        assert body["old_key_expires_at"] is not None

    def test_rotate_unknown_key_returns_404(self, client):
        ctx = _signup_and_key(client)
        r = client.post(
            f"/portal/projects/{ctx['project_id']}/keys/fake-id/rotate",
            headers={"Authorization": f"Bearer {ctx['token']}"},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "key_not_found"

    def test_rotate_cross_tenant_is_denied(self, client):
        # NOTE: this asserted 404 historically but was already failing on
        # main before the HttpOnly-cookie work — the rotate path resolves
        # the project first via _resolve_project, which deliberately
        # returns an opaque 403 ``project_access_denied`` for cross-tenant
        # probes ("exists, you can't see it" — never leak existence).
        # Updated to match that real, intentional behaviour.
        ctx = _signup_and_key(client)
        other_r = client.post(
            "/portal/auth/signup",
            json={"email": "other@example.com", "password": "Test1234!"},
        )
        # HttpOnly migration: token is a cookie now. Clear the jar so the
        # Bearer header (not the leftover cookie) drives this request.
        other_token = other_r.cookies.get("portal_access_token")
        client.cookies.clear()
        r = client.post(
            f"/portal/projects/{ctx['project_id']}/keys/{ctx['key_id']}/rotate",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "project_access_denied"
