"""P8-03: Admin authorization enforcement tests.

Verifies:
- Admin endpoints require auth (401 without header)
- Admin endpoints require admin role (403 for user role)
- Admin role succeeds
"""

import pytest
from fastapi import Header
from fastapi.testclient import TestClient

from app.api.deps import ResolvedApiKey, get_resolved_api_key, require_admin
from app.main import app as main_app


def _make_mock(role: str):
    auth = ResolvedApiKey(
        key_id="k", tenant_id="t", project_id="p", key_prefix="gw_", role=role
    )

    async def _mock(authorization: str | None = Header(default=None)):
        from app.core.exceptions import AuthenticationError
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("Missing auth")
        return auth

    return _mock, auth


class TestAdminAuthRequired:
    def test_no_auth_returns_401(self):
        # Temporarily remove the require_admin override so real auth runs
        old = main_app.dependency_overrides.get(require_admin)
        main_app.dependency_overrides.pop(require_admin, None)
        old_auth = main_app.dependency_overrides.get(get_resolved_api_key)
        main_app.dependency_overrides.pop(get_resolved_api_key, None)
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get("/admin/tenants")
            assert resp.status_code == 401
        finally:
            if old is not None:
                main_app.dependency_overrides[require_admin] = old
            if old_auth is not None:
                main_app.dependency_overrides[get_resolved_api_key] = old_auth


class TestAdminRoleEnforcement:
    def test_user_role_returns_403(self):
        mock_fn, _ = _make_mock("user")
        main_app.dependency_overrides[get_resolved_api_key] = mock_fn
        old_admin = main_app.dependency_overrides.pop(require_admin, None)
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get("/admin/tenants", headers={"Authorization": "Bearer x"})
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "authorization_error"
        finally:
            from tests.conftest import mock_resolved_api_key, MOCK_AUTH
            main_app.dependency_overrides[get_resolved_api_key] = mock_resolved_api_key
            if old_admin is not None:
                main_app.dependency_overrides[require_admin] = old_admin
            else:
                main_app.dependency_overrides[require_admin] = lambda: MOCK_AUTH

    def test_admin_role_succeeds(self):
        mock_fn, _ = _make_mock("admin")
        main_app.dependency_overrides[get_resolved_api_key] = mock_fn
        old_admin = main_app.dependency_overrides.pop(require_admin, None)
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get("/admin/tenants", headers={"Authorization": "Bearer x"})
            assert resp.status_code == 200
        finally:
            from tests.conftest import mock_resolved_api_key, MOCK_AUTH
            main_app.dependency_overrides[get_resolved_api_key] = mock_resolved_api_key
            if old_admin is not None:
                main_app.dependency_overrides[require_admin] = old_admin
            else:
                main_app.dependency_overrides[require_admin] = lambda: MOCK_AUTH

    def test_project_admin_role_succeeds(self):
        mock_fn, _ = _make_mock("project_admin")
        main_app.dependency_overrides[get_resolved_api_key] = mock_fn
        old_admin = main_app.dependency_overrides.pop(require_admin, None)
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get("/admin/tenants", headers={"Authorization": "Bearer x"})
            assert resp.status_code == 200
        finally:
            from tests.conftest import mock_resolved_api_key, MOCK_AUTH
            main_app.dependency_overrides[get_resolved_api_key] = mock_resolved_api_key
            if old_admin is not None:
                main_app.dependency_overrides[require_admin] = old_admin
            else:
                main_app.dependency_overrides[require_admin] = lambda: MOCK_AUTH
