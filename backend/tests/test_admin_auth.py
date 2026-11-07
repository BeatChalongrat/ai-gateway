"""P8-03 + Phase 2B Commit 9: Admin authz enforcement tests.

Updated for Commit 9 — admin endpoints now reject Bearer tokens
outright and accept only the ``admin_session`` cookie. The three
tests below therefore assert:

* no auth at all → 401
* a Bearer header of any shape → 401 (NOT 403 — the channel itself
  is closed, role isn't even consulted)
* a request that hits the dep-override path (simulating a valid
  session) → 200
"""

import pytest
from fastapi import Header
from fastapi.testclient import TestClient

from app.api.deps import (
    ResolvedApiKey,
    get_resolved_api_key,
    require_admin,
    require_admin_session,
)
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
        # Temporarily remove all auth overrides so the real deps run.
        old_session = main_app.dependency_overrides.pop(require_admin_session, None)
        old_admin = main_app.dependency_overrides.pop(require_admin, None)
        old_auth = main_app.dependency_overrides.pop(get_resolved_api_key, None)
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get("/admin/tenants")
            assert resp.status_code == 401
        finally:
            if old_session is not None:
                main_app.dependency_overrides[require_admin_session] = old_session
            if old_admin is not None:
                main_app.dependency_overrides[require_admin] = old_admin
            if old_auth is not None:
                main_app.dependency_overrides[get_resolved_api_key] = old_auth


class TestAdminRoleEnforcement:
    def test_bearer_header_is_rejected_401_not_403(self):
        """Phase 2B Commit 9 closed the Bearer channel on /admin/*.
        Any Bearer is a 401 regardless of the role claim inside it."""
        old_session = main_app.dependency_overrides.pop(require_admin_session, None)
        old_admin = main_app.dependency_overrides.pop(require_admin, None)
        old_auth = main_app.dependency_overrides.pop(get_resolved_api_key, None)
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get(
                "/admin/tenants",
                headers={"Authorization": "Bearer gw_some_key"},
            )
            assert resp.status_code == 401
            body = resp.json()
            assert "Bearer tokens are not accepted" in str(body)
        finally:
            if old_session is not None:
                main_app.dependency_overrides[require_admin_session] = old_session
            if old_admin is not None:
                main_app.dependency_overrides[require_admin] = old_admin
            if old_auth is not None:
                main_app.dependency_overrides[get_resolved_api_key] = old_auth

    def test_admin_role_succeeds_via_session(self):
        """Happy-path admin access through the cookie-session dep."""
        # The conftest default override already returns MOCK_AUTH for
        # require_admin_session — confirm it lets the route through.
        client = TestClient(main_app, raise_server_exceptions=False)
        resp = client.get("/admin/tenants")
        assert resp.status_code == 200

    def test_session_role_viewer_returns_403(self):
        """If the session carries a role below PROJECT_ADMIN, the
        route responds 403. Exercised via a targeted override that
        simulates a viewer-role cookie."""
        viewer = ResolvedApiKey(
            key_id="session:u-viewer", tenant_id="t-a",
            project_id="__admin__", key_prefix="sess:", role="viewer",
        )

        def _viewer_dep():
            from app.core.exceptions import AuthorizationError
            raise AuthorizationError(
                "Insufficient permissions for admin operations.",
                details={"role": "viewer", "required": "project_admin"},
            )

        old = main_app.dependency_overrides.get(require_admin_session)
        main_app.dependency_overrides[require_admin_session] = _viewer_dep
        try:
            client = TestClient(main_app, raise_server_exceptions=False)
            resp = client.get("/admin/tenants")
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "authorization_error"
        finally:
            if old is not None:
                main_app.dependency_overrides[require_admin_session] = old
