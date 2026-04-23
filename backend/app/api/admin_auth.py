"""Admin-route auth: declarative decorators + startup boot-check.

Phase 2A, Commit 2. See ``AUTH_REDESIGN_DESIGN.md`` §5.

Four decorators, one boot-check:

* ``@require_role(min_role)`` — session role must be >= min_role.
* ``@scope_to_tenant`` — injects ``tenant_id`` kwarg from session so
  the service layer filters by it. Service layer MUST treat
  ``tenant_id=None`` as an empty filter (zero rows), never as "no
  filter" — that's the behaviour contract.
* ``@cross_tenant(reason)`` — opt-out for genuinely cross-tenant
  super-admin tooling. Only callers with ``role == ADMIN`` are
  allowed through, and every invocation writes an audit entry.
* ``@public`` — opt-out marker for intentionally unauthenticated
  admin-namespace endpoints (status pages, OAuth start, etc.).

``check_admin_routes_decorated(app)`` is invoked at startup by
``app.main`` AFTER all routers are included. It walks every route,
filters those under ``/admin/`` (case-insensitive), and raises
``RuntimeError`` if any endpoint function lacks one of the four
markers. Errors list EVERY missing route so the operator fixes all
of them on a single boot cycle.

The decorators do NOT rely on session middleware populating
``request.state`` — each attaches a ``Depends(get_admin_session)`` to
the endpoint signature. Tests override ``get_admin_session`` via
``app.dependency_overrides``.
"""

from __future__ import annotations

import functools
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.domain.rbac import Role


_AUTH_MARK_ATTR = "__admin_auth__"
_ADMIN_PATH_PREFIX = "/admin/"

_log = logging.getLogger("admin_auth")


MarkKind = Literal["require_role", "scope_to_tenant", "cross_tenant", "public"]


@dataclass(frozen=True)
class AuthMark:
    """Boot-check marker attached to every admin endpoint."""

    kind: MarkKind
    min_role: Optional[Role] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Session dependency (stubbed here; Commit 3 fills in the Redis wiring)
# ---------------------------------------------------------------------------


def get_admin_session(request: Request) -> dict:
    """Dependency: return the validated admin session payload.

    Commit 2 installs this as the stable injection point. Commit 3
    wires it to ``SessionManager.verify_cookie`` + CSRF checks. For
    now, any request that reaches here without a dependency override
    is unauthenticated, so raise.
    """
    raise AuthenticationError(
        "admin session not available — session middleware not installed"
    )


# ---------------------------------------------------------------------------
# Audit hook — Commit 7 replaces this with a DB-backed writer
# ---------------------------------------------------------------------------


def _record_cross_tenant_access(
    *,
    user_id: str,
    session_tenant_id: str | None,
    reason: str,
    method: str,
    path: str,
) -> None:
    """Stub audit entry. Commit 7 replaces with admin_audit_log insert."""
    _log.info(
        "cross_tenant_access user=%s reason=%r method=%s path=%s",
        user_id, reason, method, path,
    )


# ---------------------------------------------------------------------------
# Decorator builders
# ---------------------------------------------------------------------------


def _mark(fn: Callable, mark: AuthMark) -> None:
    setattr(fn, _AUTH_MARK_ATTR, mark)


def _attach_dependency(fn: Callable, dep: Callable, mark: AuthMark) -> Callable:
    """Return a wrapper of ``fn`` that declares ``dep`` as a kwarg
    Depends so FastAPI runs the dep before invoking the real endpoint.
    The injected kwarg is popped before dispatching to ``fn``."""
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    injected_name = "_admin_auth_session"
    injected = inspect.Parameter(
        injected_name,
        kind=inspect.Parameter.KEYWORD_ONLY,
        default=Depends(dep),
        annotation=dict,
    )
    new_sig = sig.replace(parameters=params + [injected])

    is_async = inspect.iscoroutinefunction(fn)
    if is_async:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            kwargs.pop(injected_name, None)
            return await fn(*args, **kwargs)
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            kwargs.pop(injected_name, None)
            return fn(*args, **kwargs)

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    _mark(wrapper, mark)
    return wrapper


def _role_of(session: dict) -> Role:
    raw = (session or {}).get("role", "USER")
    try:
        return Role[str(raw).upper()]
    except KeyError:
        return Role.VIEWER  # lowest — denied by default


def require_role(min_role: Role) -> Callable:
    """Decorator: session role must be >= ``min_role``."""
    mark = AuthMark(kind="require_role", min_role=min_role)

    def check(session: dict = Depends(get_admin_session)) -> dict:
        actual = _role_of(session)
        if actual < min_role:
            raise AuthorizationError(
                f"Role {actual.name} cannot access endpoint "
                f"(requires {min_role.name}).",
                details={"required": min_role.name, "actual": actual.name},
            )
        return session

    def decorator(fn: Callable) -> Callable:
        return _attach_dependency(fn, check, mark)

    return decorator


def scope_to_tenant(fn: Callable) -> Callable:
    """Decorator: inject ``tenant_id`` kwarg from session payload.

    The decorated endpoint MUST declare a ``tenant_id`` parameter.
    FastAPI never sees that parameter — the wrapper strips it from the
    exposed signature (so it isn't validated as a query/body field)
    and substitutes the session's ``tenant_id`` before calling the
    endpoint body.
    """
    mark = AuthMark(kind="scope_to_tenant")

    def check(session: dict = Depends(get_admin_session)) -> dict:
        return session  # session already validated by session dep

    sig = inspect.signature(fn)
    if "tenant_id" not in sig.parameters:
        raise TypeError(
            "@scope_to_tenant requires the endpoint to declare a "
            f"`tenant_id` parameter: {fn.__qualname__}"
        )
    # Remove ``tenant_id`` from the exposed signature — FastAPI would
    # otherwise demand it as a query/path parameter.
    visible_params = [p for n, p in sig.parameters.items() if n != "tenant_id"]
    injected_name = "_admin_auth_session"
    injected = inspect.Parameter(
        injected_name,
        kind=inspect.Parameter.KEYWORD_ONLY,
        default=Depends(check),
        annotation=dict,
    )
    new_sig = sig.replace(parameters=visible_params + [injected])

    is_async = inspect.iscoroutinefunction(fn)
    if is_async:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            session = kwargs.pop(injected_name, None) or {}
            kwargs["tenant_id"] = session.get("tenant_id")
            return await fn(*args, **kwargs)
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            session = kwargs.pop(injected_name, None) or {}
            kwargs["tenant_id"] = session.get("tenant_id")
            return fn(*args, **kwargs)

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    _mark(wrapper, mark)
    return wrapper


def cross_tenant(*, reason: str) -> Callable:
    """Decorator: opt-out for genuinely cross-tenant super-admin tooling.

    Only callers with ``role == ADMIN`` pass. Every successful
    invocation records an audit entry (reason + user + path).
    """
    if not reason or not reason.strip():
        raise ValueError("cross_tenant requires a non-empty reason")
    mark = AuthMark(kind="cross_tenant", reason=reason.strip())

    def check(
        request: Request,
        session: dict = Depends(get_admin_session),
    ) -> dict:
        actual = _role_of(session)
        if actual < Role.ADMIN:
            raise AuthorizationError(
                "Cross-tenant access is restricted to global ADMIN role.",
                details={"required": "ADMIN", "actual": actual.name},
            )
        _record_cross_tenant_access(
            user_id=session.get("user_id", "?"),
            session_tenant_id=session.get("tenant_id"),
            reason=mark.reason or "",
            method=request.method,
            path=request.url.path,
        )
        return session

    def decorator(fn: Callable) -> Callable:
        return _attach_dependency(fn, check, mark)

    return decorator


def public(fn: Callable) -> Callable:
    """Decorator: genuinely public endpoint; no session required."""
    _mark(fn, AuthMark(kind="public"))
    return fn


# ---------------------------------------------------------------------------
# Boot-check
# ---------------------------------------------------------------------------


def _endpoint_has_mark(endpoint: Callable) -> bool:
    """True if ``endpoint`` (or anything it wraps) carries a marker."""
    seen = set()
    current: Any = endpoint
    # Walk functools.wraps chain to catch markers attached to inner fns.
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if hasattr(current, _AUTH_MARK_ATTR):
            return True
        current = getattr(current, "__wrapped__", None)
    return False


def check_admin_routes_decorated(app: FastAPI) -> None:
    """Scan routes and fail boot if any ``/admin/*`` lacks a marker.

    Lists ALL missing routes — never short-circuits on the first one.
    """
    missing: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.lower().startswith(_ADMIN_PATH_PREFIX):
            continue
        endpoint = route.endpoint
        if not _endpoint_has_mark(endpoint):
            methods = ",".join(sorted(route.methods or [])) or "ANY"
            missing.append(f"{methods} {route.path} -> {endpoint.__qualname__}")
    if missing:
        raise RuntimeError(
            "STARTUP BLOCKED — admin routes without auth markers:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\n\nEvery /admin/* route must use one of: @require_role, "
            "@scope_to_tenant, @cross_tenant, @public."
        )
