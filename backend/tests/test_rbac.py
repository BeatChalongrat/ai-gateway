"""P8-02: RBAC domain model and permission tests."""

import pytest

from app.domain.rbac import (
    PERM_ADMIN_READ, PERM_ADMIN_WRITE, PERM_KEY_MANAGE,
    PERM_TENANT_MANAGE, PERM_USAGE_READ,
    Role, has_permission, has_role_level,
)


class TestRoles:
    def test_role_ordering(self):
        assert Role.VIEWER < Role.USER < Role.PROJECT_ADMIN < Role.TENANT_ADMIN < Role.ADMIN

    def test_admin_has_all_permissions(self):
        for perm in [PERM_ADMIN_READ, PERM_ADMIN_WRITE, PERM_TENANT_MANAGE, PERM_KEY_MANAGE, PERM_USAGE_READ]:
            assert has_permission(Role.ADMIN, perm)

    def test_viewer_only_reads_usage(self):
        assert has_permission(Role.VIEWER, PERM_USAGE_READ)
        assert not has_permission(Role.VIEWER, PERM_ADMIN_WRITE)
        assert not has_permission(Role.VIEWER, PERM_KEY_MANAGE)

    def test_user_cannot_manage_tenants(self):
        assert not has_permission(Role.USER, PERM_TENANT_MANAGE)

    def test_tenant_admin_can_manage_keys(self):
        assert has_permission(Role.TENANT_ADMIN, PERM_KEY_MANAGE)

    def test_project_admin_can_read_admin(self):
        assert has_permission(Role.PROJECT_ADMIN, PERM_ADMIN_READ)
        assert not has_permission(Role.PROJECT_ADMIN, PERM_ADMIN_WRITE)


class TestRoleLevel:
    def test_admin_meets_admin(self):
        assert has_role_level(Role.ADMIN, Role.ADMIN)

    def test_user_does_not_meet_admin(self):
        assert not has_role_level(Role.USER, Role.ADMIN)

    def test_tenant_admin_meets_project_admin(self):
        assert has_role_level(Role.TENANT_ADMIN, Role.PROJECT_ADMIN)


class TestResolvedApiKeyRole:
    def test_default_role_is_user(self):
        from app.api.deps import ResolvedApiKey
        r = ResolvedApiKey(key_id="k", tenant_id="t", project_id="p", key_prefix="gw_")
        assert r.role == "user"

    def test_admin_role(self):
        from app.api.deps import ResolvedApiKey
        r = ResolvedApiKey(key_id="k", tenant_id="t", project_id="p", key_prefix="gw_", role="admin")
        assert r.role == "admin"
