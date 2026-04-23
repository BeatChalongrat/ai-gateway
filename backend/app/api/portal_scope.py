"""Portal-route scope markers + startup boot-check (Concern 2A).

Phase 3 Commit 23. Mirrors the admin boot-check pattern from
``admin_auth.py``. Every route under ``/portal/*`` MUST declare its
scope via one of three decorators:

* ``@scope_to_portal_tenant`` — caller's session tenant_id is the
  only tenant whose data this route reads or writes. Default for
  portal routes.
* ``@explicit_cross_portal_tenant(reason=...)`` — intentionally
  non-tenant-scoped (e.g., global SKUs, public marketing data).
  Requires a short prose reason so grep -r finds every exception.
* ``@public_portal_route`` — unauthenticated entry points: signup,
  login, OAuth start/callback, public settings.

``check_portal_routes_decorated(app)`` walks every registered route
at startup, filters the ``/portal/*`` subset, and raises
``RuntimeError`` listing every undeclared route. Boot fails loud so
a forgotten decorator can't silently ship.

The markers are attribute-only — they do NOT wrap or alter the
endpoint's behaviour. Tenant scoping itself is enforced by the
service / DB layer (portal_user_id filters, tenant_id assignments).
This module exists to guarantee no route escapes that contract
without an explicit opt-out the reviewer can see in the diff.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute


_PORTAL_SCOPE_ATTR = "__portal_scope__"
_PORTAL_PATH_PREFIX = "/portal/"

_log = logging.getLogger("portal_scope")


ScopeKind = Literal["tenant", "cross_tenant", "public"]


@dataclass(frozen=True)
class PortalScope:
    """Boot-check marker attached to every portal endpoint."""

    kind: ScopeKind
    reason: Optional[str] = None


def _attach(endpoint: Callable, scope: PortalScope) -> Callable:
    setattr(endpoint, _PORTAL_SCOPE_ATTR, scope)
    return endpoint


def scope_to_portal_tenant(fn: Callable) -> Callable:
    """Mark a route as tenant-scoped.

    Contract: the route reads / writes data belonging ONLY to the
    caller's tenant (derived from the session). The implementation
    is responsible for honouring this — this decorator just declares
    intent.
    """
    return _attach(fn, PortalScope(kind="tenant"))


def explicit_cross_portal_tenant(*, reason: str) -> Callable:
    """Mark a route as intentionally non-tenant-scoped.

    Use sparingly. ``reason`` is required and should name the
    specific data class crossing tenant boundaries (e.g. "global
    token pack SKUs", "public marketing settings").
    """
    if not reason or not reason.strip():
        raise ValueError("explicit_cross_portal_tenant requires a reason")
    scope = PortalScope(kind="cross_tenant", reason=reason.strip())

    def decorator(fn: Callable) -> Callable:
        return _attach(fn, scope)

    return decorator


def public_portal_route(fn: Callable) -> Callable:
    """Mark a route as unauthenticated / public (signup, OAuth, etc)."""
    return _attach(fn, PortalScope(kind="public"))


def _endpoint_has_scope(endpoint: Callable) -> bool:
    """True if ``endpoint`` (or anything it wraps) carries a scope
    marker. Walks ``__wrapped__`` so decorator stacks work."""
    seen: set[int] = set()
    current = endpoint
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if hasattr(current, _PORTAL_SCOPE_ATTR):
            return True
        current = getattr(current, "__wrapped__", None)
    return False


def check_portal_routes_decorated(app: FastAPI) -> list[str]:
    """Boot-check: every /portal/* route must carry a scope marker.

    Raises ``RuntimeError`` with the full list of undeclared routes
    so the operator can fix them all in one cycle. Returns the list
    of already-declared portal routes on success (useful for logging
    the inventory).
    """
    missing: list[str] = []
    declared: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.lower().startswith(_PORTAL_PATH_PREFIX):
            continue
        endpoint = route.endpoint
        if _endpoint_has_scope(endpoint):
            declared.append(f"{route.methods} {route.path}")
        else:
            missing.append(
                f"  {','.join(sorted(route.methods or []))} {route.path} "
                f"(endpoint: {endpoint.__module__}.{endpoint.__qualname__})"
            )

    if missing:
        raise RuntimeError(
            "Portal routes missing scope declaration. Every /portal/* "
            "route must be decorated with @scope_to_portal_tenant, "
            "@explicit_cross_portal_tenant(reason=...), or "
            "@public_portal_route.\n"
            "Undeclared routes:\n" + "\n".join(missing)
        )
    return declared
