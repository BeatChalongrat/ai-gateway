"""Phase 3 Commit 23 — portal boot-check (Concern 2A) verification.

The boot-check in ``app.api.portal_scope`` crashes app startup if any
/portal/* route is missing a scope decorator. This test suite pins
three things:

1. The production app already has every /portal/* route decorated
   (the startup import of app.main, which runs the check, must have
   succeeded — test imports confirm).
2. Adding a new undecorated /portal/* route would fail the check.
3. Each of the three decorators correctly marks a route as declared.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.api.portal_scope import (
    PortalScope,
    check_portal_routes_decorated,
    explicit_cross_portal_tenant,
    public_portal_route,
    scope_to_portal_tenant,
    _PORTAL_SCOPE_ATTR,
)


class TestDecoratorsAttachMarker:
    def test_scope_to_portal_tenant_tags(self):
        @scope_to_portal_tenant
        def handler():
            pass

        mark = getattr(handler, _PORTAL_SCOPE_ATTR)
        assert isinstance(mark, PortalScope)
        assert mark.kind == "tenant"

    def test_explicit_cross_portal_tenant_requires_reason(self):
        with pytest.raises(ValueError, match="requires a reason"):
            explicit_cross_portal_tenant(reason="")

    def test_explicit_cross_portal_tenant_tags_with_reason(self):
        @explicit_cross_portal_tenant(reason="global pack SKUs")
        def handler():
            pass

        mark = getattr(handler, _PORTAL_SCOPE_ATTR)
        assert mark.kind == "cross_tenant"
        assert mark.reason == "global pack SKUs"

    def test_public_portal_route_tags(self):
        @public_portal_route
        def handler():
            pass

        mark = getattr(handler, _PORTAL_SCOPE_ATTR)
        assert mark.kind == "public"


class TestBootCheckRejectsUndecoratedRoute:
    def test_missing_marker_raises_runtime_error(self):
        app = FastAPI()

        @app.get("/portal/orphan")
        def orphan():
            return {}

        with pytest.raises(RuntimeError, match="missing scope declaration"):
            check_portal_routes_decorated(app)

    def test_mixed_missing_error_lists_every_route(self):
        app = FastAPI()

        @app.get("/portal/a")
        def a():
            return {}

        @app.get("/portal/b")
        def b():
            return {}

        @app.get("/portal/decorated")
        @scope_to_portal_tenant
        def c():
            return {}

        with pytest.raises(RuntimeError) as exc:
            check_portal_routes_decorated(app)
        msg = str(exc.value)
        assert "/portal/a" in msg
        assert "/portal/b" in msg
        # The decorated one must NOT appear in the missing list.
        assert "/portal/decorated " not in msg

    def test_decorated_route_passes(self):
        app = FastAPI()

        @app.get("/portal/thing")
        @scope_to_portal_tenant
        def thing():
            return {}

        # No raise — returns list of declared.
        declared = check_portal_routes_decorated(app)
        assert any("/portal/thing" in d for d in declared)

    def test_non_portal_routes_are_ignored(self):
        app = FastAPI()

        @app.get("/admin/unrelated")
        def x():
            return {}

        @app.get("/v1/public")
        def y():
            return {}

        # No /portal/* routes at all → empty pass.
        assert check_portal_routes_decorated(app) == []


class TestProductionAppPassesBootCheck:
    def test_real_app_already_decorated(self):
        """Importing app.main runs the boot-check at module load; if
        this import succeeds the check passed. We re-run it here for
        belt-and-braces and to count the declared routes."""
        from app.main import app

        declared = check_portal_routes_decorated(app)
        # At least the handful of /portal/* routes we expect should
        # be visible. Not asserting an exact count — the list grows.
        paths_seen = " ".join(declared)
        assert "/portal/auth/signup" in paths_seen
        assert "/portal/team/invites" in paths_seen
        assert "/portal/projects" in paths_seen
        assert "/portal/me" in paths_seen
        assert "/portal/billing/packs" in paths_seen
