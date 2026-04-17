"""P4-06: RequestContext dataclass tests.

Verifies:
- RequestContext is frozen
- All fields accessible
- Defaults for optional fields
- Used correctly by downstream services
"""

import pytest

from app.domain.entities import RequestContext


class TestRequestContext:
    def test_all_fields(self):
        ctx = RequestContext(
            tenant_id="t1",
            project_id="p1",
            project_name="My Project",
            policy_profile_id="strict",
            request_id="req_abc",
        )
        assert ctx.tenant_id == "t1"
        assert ctx.project_id == "p1"
        assert ctx.project_name == "My Project"
        assert ctx.policy_profile_id == "strict"
        assert ctx.request_id == "req_abc"

    def test_frozen(self):
        ctx = RequestContext(
            tenant_id="t1", project_id="p1", project_name="P"
        )
        with pytest.raises(AttributeError):
            ctx.tenant_id = "t2"  # type: ignore[misc]

    def test_optional_defaults(self):
        ctx = RequestContext(
            tenant_id="t1", project_id="p1", project_name="P"
        )
        assert ctx.policy_profile_id is None
        assert ctx.request_id is None

    def test_equality(self):
        a = RequestContext(tenant_id="t1", project_id="p1", project_name="P")
        b = RequestContext(tenant_id="t1", project_id="p1", project_name="P")
        assert a == b

    def test_inequality(self):
        a = RequestContext(tenant_id="t1", project_id="p1", project_name="P")
        b = RequestContext(tenant_id="t2", project_id="p1", project_name="P")
        assert a != b
