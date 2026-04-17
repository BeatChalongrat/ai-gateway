"""P4-04: API Key authentication dependency tests.

Verifies:
- Valid key returns ResolvedApiKey
- Missing Authorization header → 401
- Malformed header (no Bearer prefix) → 401
- Empty token after Bearer → 401
- Invalid/unknown key → 401
- Revoked key → 401
- Expired key → 401
- Uses existing AuthenticationError (not raw HTTPException)
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key, get_db_session
from app.core.exceptions import AuthenticationError
from app.middleware.error_handler import handle_gateway_error
from app.models.api_key import ApiKeyORM
from app.models.project import ProjectORM
from app.models.tenant import TenantORM
from app.services.api_key_service import ApiKeyService
import app.models  # noqa: F401


def _create_test_app(session_override):
    """Build a minimal FastAPI app with auth dependency and session override."""
    test_app = FastAPI()
    test_app.add_exception_handler(AuthenticationError, handle_gateway_error)

    def _override_session():
        try:
            yield session_override
        finally:
            pass  # Don't close — test owns the session lifecycle

    test_app.dependency_overrides[get_db_session] = _override_session

    @test_app.get("/protected")
    async def protected(auth: ResolvedApiKey = Depends(get_resolved_api_key)):
        return {
            "key_id": auth.key_id,
            "tenant_id": auth.tenant_id,
            "project_id": auth.project_id,
            "key_prefix": auth.key_prefix,
        }

    return test_app


def _seed_tenant_and_project(session):
    tid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    session.add(TenantORM(tenant_id=tid, name="Auth Tenant", slug=f"auth-{tid[:8]}"))
    session.flush()
    session.add(ProjectORM(project_id=pid, tenant_id=tid, name="Auth Project"))
    session.flush()
    return tid, pid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuthDependency:
    @pytest.fixture(autouse=True)
    def setup(self, db_session):
        self.session = db_session
        self.app = _create_test_app(db_session)
        self.client = TestClient(self.app)
        self.svc = ApiKeyService()

    def _create_key(self, **kwargs):
        tid, pid = _seed_tenant_and_project(self.session)
        return self.svc.create_key(
            self.session,
            tenant_id=kwargs.get("tenant_id", tid),
            project_id=kwargs.get("project_id", pid),
            name=kwargs.get("name", "Test"),
            expires_at=kwargs.get("expires_at"),
        )

    def test_valid_key_returns_resolved(self):
        raw_key, created = self._create_key()
        resp = self.client.get(
            "/protected",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_id"] == created.key_id
        assert data["tenant_id"] == created.tenant_id
        assert data["project_id"] == created.project_id

    def test_missing_header_returns_401(self):
        resp = self.client.get("/protected")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "authentication_error"

    def test_no_bearer_prefix_returns_401(self):
        resp = self.client.get(
            "/protected",
            headers={"Authorization": "Token abc123"},
        )
        assert resp.status_code == 401

    def test_empty_bearer_returns_401(self):
        resp = self.client.get(
            "/protected",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    def test_unknown_key_returns_401(self):
        resp = self.client.get(
            "/protected",
            headers={"Authorization": "Bearer gw_totally_invalid_key"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "authentication_error"

    def test_revoked_key_returns_401(self):
        raw_key, created = self._create_key()
        self.svc.revoke_key(self.session, created.key_id)
        resp = self.client.get(
            "/protected",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 401

    def test_expired_key_returns_401(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        raw_key, _ = self._create_key(expires_at=past)
        resp = self.client.get(
            "/protected",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 401


class TestResolvedApiKeyDataclass:
    def test_frozen(self):
        resolved = ResolvedApiKey(
            key_id="k1", tenant_id="t1", project_id="p1", key_prefix="gw_test_"
        )
        with pytest.raises(AttributeError):
            resolved.key_id = "k2"  # type: ignore[misc]

    def test_fields(self):
        resolved = ResolvedApiKey(
            key_id="k1", tenant_id="t1", project_id="p1", key_prefix="gw_test_"
        )
        assert resolved.key_id == "k1"
        assert resolved.tenant_id == "t1"
        assert resolved.project_id == "p1"
        assert resolved.key_prefix == "gw_test_"
