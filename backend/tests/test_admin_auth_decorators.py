"""Phase 2A — Commit 2: admin_auth decorators + boot-check (TDD).

Pins the public contract of ``app.api.admin_auth``:

* ``@require_role(min_role)`` — rejects sessions whose role < min_role
  with an AuthorizationError (403). Marks the endpoint for boot-check.
* ``@scope_to_tenant`` — injects the caller's tenant_id from the
  session so the endpoint body can use it as a SQL filter. Marks for
  boot-check.
* ``@cross_tenant(reason)`` — opt-out used by genuinely cross-tenant
  super-admin tooling; records an audit-log entry. Marks for boot-check.
* ``@public`` — opt-out marker for endpoints that deliberately need
  no auth (e.g. a status page). Marks for boot-check but does not
  attach any session check.

* ``check_admin_routes_decorated(app)`` — called at startup. Walks
  every registered route, filters those under ``/admin/`` (case-
  insensitive), and raises RuntimeError listing ALL routes whose
  endpoint function lacks one of the 4 markers.

The session dependency is overridden in these tests to bypass Redis.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_auth import (
    AuthMark,
    check_admin_routes_decorated,
    cross_tenant,
    get_admin_session,
    public,
    require_role,
    scope_to_tenant,
)
from app.domain.rbac import Role


# ---------------------------------------------------------------------------
# Session stub helpers
# ---------------------------------------------------------------------------


def _session(role: str = "ADMIN", tenant_id: str = "tenant-a", user_id: str = "u1"):
    return {
        "session_id": "sess-test",
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
    }


def _build_app(session_payload: dict | None) -> FastAPI:
    """FastAPI app with get_admin_session overridden + error handlers wired."""
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )

    app = FastAPI()
    app.add_exception_handler(GatewayError, handle_gateway_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)

    if session_payload is None:
        def _unauth():
            from app.core.exceptions import AuthenticationError
            raise AuthenticationError("no session")
        app.dependency_overrides[get_admin_session] = _unauth
    else:
        app.dependency_overrides[get_admin_session] = lambda: session_payload
    return app


# ---------------------------------------------------------------------------
# Marker attachment
# ---------------------------------------------------------------------------


class TestMarkers:
    def test_require_role_marks_fn(self):
        @require_role(Role.TENANT_ADMIN)
        def handler():
            return {"ok": True}

        mark: AuthMark = handler.__admin_auth__
        assert mark.kind == "require_role"
        assert mark.min_role == Role.TENANT_ADMIN

    def test_scope_to_tenant_marks_fn(self):
        @scope_to_tenant
        def handler(tenant_id: str):
            return {"tenant": tenant_id}

        assert handler.__admin_auth__.kind == "scope_to_tenant"

    def test_cross_tenant_marks_fn(self):
        @cross_tenant(reason="super-admin audit export")
        def handler():
            return {"ok": True}

        mark = handler.__admin_auth__
        assert mark.kind == "cross_tenant"
        assert mark.reason == "super-admin audit export"

    def test_public_marks_fn(self):
        @public
        def handler():
            return {"ok": True}

        assert handler.__admin_auth__.kind == "public"

    def test_cross_tenant_requires_reason(self):
        """Empty reason rejected at decoration time — prevents
        someone silencing the boot-check by stubbing a reason later.
        """
        with pytest.raises(ValueError, match="reason"):
            cross_tenant(reason="")


# ---------------------------------------------------------------------------
# @require_role enforcement
# ---------------------------------------------------------------------------


class TestRequireRoleEnforcement:
    def test_role_below_threshold_rejected(self):
        app = _build_app(_session(role="USER"))

        @require_role(Role.TENANT_ADMIN)
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        client = TestClient(app)
        r = client.get("/admin/x")
        assert r.status_code == 403, r.text

    def test_role_at_threshold_accepted(self):
        app = _build_app(_session(role="TENANT_ADMIN"))

        @require_role(Role.TENANT_ADMIN)
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_role_above_threshold_accepted(self):
        app = _build_app(_session(role="ADMIN"))

        @require_role(Role.PROJECT_ADMIN)
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 200

    def test_no_session_rejected_401(self):
        app = _build_app(session_payload=None)

        @require_role(Role.USER)
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 401

    def test_unknown_role_treated_as_lowest(self):
        app = _build_app(_session(role="SOMETHING_MADE_UP"))

        @require_role(Role.USER)
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# @scope_to_tenant enforcement
# ---------------------------------------------------------------------------


class TestScopeToTenantEnforcement:
    def test_tenant_id_injected_from_session(self):
        app = _build_app(_session(tenant_id="tenant-a"))

        @scope_to_tenant
        def handler(tenant_id: str):
            return {"tenant": tenant_id}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 200
        assert r.json() == {"tenant": "tenant-a"}

    def test_none_tenant_empty_filter_not_global(self):
        """If session carries no tenant_id, scope_to_tenant must pass
        None through — the service layer is contracted to treat None
        as 'empty filter' (zero rows), NOT as 'no filter' (global).
        """
        app = _build_app(_session(tenant_id=None))

        @scope_to_tenant
        def handler(tenant_id):
            # Echo type+value to test
            return {"tenant_id": tenant_id, "is_none": tenant_id is None}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 200
        assert r.json()["is_none"] is True

    def test_no_session_rejected_401(self):
        app = _build_app(session_payload=None)

        @scope_to_tenant
        def handler(tenant_id: str):
            return {"tenant": tenant_id}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# @cross_tenant enforcement
# ---------------------------------------------------------------------------


class TestCrossTenantEnforcement:
    def test_only_global_admin_can_invoke(self):
        app = _build_app(_session(role="TENANT_ADMIN", tenant_id="tenant-a"))

        @cross_tenant(reason="audit")
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 403, r.text

    def test_global_admin_allowed(self):
        app = _build_app(_session(role="ADMIN", tenant_id="tenant-a"))

        @cross_tenant(reason="audit")
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        r = TestClient(app).get("/admin/x")
        assert r.status_code == 200

    def test_audit_hook_called_on_cross_tenant_access(self, monkeypatch):
        """The decorator writes an audit trail entry whenever the
        endpoint executes. Commit 7 will extend the schema; for now we
        only prove the hook is invoked with the right fields.
        """
        from app.api import admin_auth

        recorded: list[dict] = []
        monkeypatch.setattr(
            admin_auth,
            "_record_cross_tenant_access",
            lambda **kw: recorded.append(kw),
        )

        app = _build_app(_session(role="ADMIN", tenant_id="tenant-a"))

        @cross_tenant(reason="super-admin audit export")
        def handler():
            return {"ok": True}

        app.get("/admin/x")(handler)
        TestClient(app).get("/admin/x")

        assert len(recorded) == 1
        assert recorded[0]["reason"] == "super-admin audit export"
        assert recorded[0]["user_id"] == "u1"
        assert recorded[0]["session_tenant_id"] == "tenant-a"


# ---------------------------------------------------------------------------
# @public
# ---------------------------------------------------------------------------


class TestPublicDecorator:
    def test_public_endpoint_requires_no_session(self):
        # No dependency_overrides — get_admin_session is NOT called.
        app = FastAPI()

        @public
        def handler():
            return {"ok": True}

        app.get("/admin/status")(handler)
        r = TestClient(app).get("/admin/status")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Boot-check
# ---------------------------------------------------------------------------


class TestBootCheck:
    def test_missing_decorator_on_admin_route_fails(self):
        app = FastAPI()

        def undecorated():
            return {"ok": True}

        app.get("/admin/test_missing_decorator")(undecorated)

        with pytest.raises(RuntimeError) as excinfo:
            check_admin_routes_decorated(app)
        msg = str(excinfo.value)
        assert "/admin/test_missing_decorator" in msg

    def test_admin_route_with_decorator_passes(self):
        app = FastAPI()

        @require_role(Role.USER)
        def ok_fn():
            return {"ok": True}

        app.get("/admin/test_with_decorator")(ok_fn)
        # Must not raise.
        check_admin_routes_decorated(app)

    def test_non_admin_route_without_decorator_ignored(self):
        app = FastAPI()

        def anyone():
            return {"ok": True}

        app.get("/public/test_no_decorator")(anyone)
        # The boot-check only scrutinises /admin/* — this is fine.
        check_admin_routes_decorated(app)

    def test_multiple_missing_all_listed(self):
        """Every missing route must appear in the error. Listing only
        the first would leave the operator fixing routes one at a time
        on successive boots.
        """
        app = FastAPI()

        def a():
            return {}

        def b():
            return {}

        def c():
            return {}

        app.get("/admin/miss_a")(a)
        app.get("/admin/miss_b")(b)
        app.get("/admin/miss_c")(c)

        with pytest.raises(RuntimeError) as excinfo:
            check_admin_routes_decorated(app)
        msg = str(excinfo.value)
        assert "/admin/miss_a" in msg
        assert "/admin/miss_b" in msg
        assert "/admin/miss_c" in msg

    def test_mixed_decorated_and_undecorated(self):
        app = FastAPI()

        @public
        def ok_fn():
            return {}

        def undec_fn():
            return {}

        app.get("/admin/ok")(ok_fn)
        app.get("/admin/missing")(undec_fn)

        with pytest.raises(RuntimeError) as excinfo:
            check_admin_routes_decorated(app)
        msg = str(excinfo.value)
        assert "/admin/missing" in msg
        assert "/admin/ok" not in msg

    def test_case_insensitive_admin_match(self):
        """``/Admin/...`` and ``/ADMIN/...`` must also be caught."""
        app = FastAPI()

        def undec():
            return {}

        app.get("/ADMIN/something")(undec)

        with pytest.raises(RuntimeError):
            check_admin_routes_decorated(app)

    def test_all_4_decorator_kinds_each_satisfy_check(self):
        app = FastAPI()

        @require_role(Role.USER)
        def r():
            return {}

        @scope_to_tenant
        def s(tenant_id: str):
            return {}

        @cross_tenant(reason="why")
        def c():
            return {}

        @public
        def p():
            return {}

        app.get("/admin/r")(r)
        app.get("/admin/s")(s)
        app.get("/admin/c")(c)
        app.get("/admin/p")(p)

        check_admin_routes_decorated(app)  # must not raise
