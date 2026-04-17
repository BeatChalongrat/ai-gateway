"""PR-01: Tenant isolation enforcement tests.

Verifies:
- Tenant-scoped admin cannot access other tenant's resources (403)
- Same-tenant access allowed
- Global admin bypasses tenant check
"""

import pytest

from app.api.deps import ResolvedApiKey, check_tenant_access
from app.core.exceptions import AuthorizationError


class TestCheckTenantAccess:
    def test_same_tenant_allowed(self):
        auth = ResolvedApiKey(key_id="k", tenant_id="t1", project_id="p", key_prefix="gw_", role="tenant_admin")
        check_tenant_access(auth, "t1")  # should not raise

    def test_cross_tenant_blocked(self):
        auth = ResolvedApiKey(key_id="k", tenant_id="t1", project_id="p", key_prefix="gw_", role="tenant_admin")
        with pytest.raises(AuthorizationError) as exc_info:
            check_tenant_access(auth, "t2")
        assert "different tenant" in exc_info.value.message

    def test_global_admin_bypasses(self):
        auth = ResolvedApiKey(key_id="k", tenant_id="t1", project_id="p", key_prefix="gw_", role="admin")
        check_tenant_access(auth, "t2")  # should not raise

    def test_project_admin_cross_tenant_blocked(self):
        auth = ResolvedApiKey(key_id="k", tenant_id="t1", project_id="p", key_prefix="gw_", role="project_admin")
        with pytest.raises(AuthorizationError):
            check_tenant_access(auth, "other-tenant")

    def test_user_role_cross_tenant_blocked(self):
        auth = ResolvedApiKey(key_id="k", tenant_id="t1", project_id="p", key_prefix="gw_", role="user")
        with pytest.raises(AuthorizationError):
            check_tenant_access(auth, "t2")

    def test_error_includes_details(self):
        auth = ResolvedApiKey(key_id="k", tenant_id="t1", project_id="p", key_prefix="gw_", role="tenant_admin")
        with pytest.raises(AuthorizationError) as exc_info:
            check_tenant_access(auth, "t2")
        assert exc_info.value.details["caller_tenant"] == "t1"
        assert exc_info.value.details["target_tenant"] == "t2"
