"""CSRF double-submit middleware for /admin/* mutations.

Phase 2B Commit 10.

Threat model: a cross-origin attacker can cause a victim's browser to
issue a request to our origin (e.g. by submitting a form on evil.example
that POSTs to api.ours). The ``admin_session`` cookie travels with that
request because ``SameSite=Lax`` still forwards top-level POST cookies
on Chrome/Safari. What they CAN'T do is read the ``csrf_token`` cookie
(same-origin policy) and echo it in an ``X-CSRF-Token`` header on a
programmatic fetch. So requiring that echo is a cheap and robust check.

Rules enforced:

* Scope: the path starts with ``/admin/`` (lowercased). Other routers
  — including the session login, portal, public chat, health — are
  skipped.
* Methods: only POST, PUT, DELETE, PATCH. GET / HEAD / OPTIONS are
  not guarded; they don't change state.
* Both ``csrf_token`` cookie AND ``X-CSRF-Token`` header must be
  present, non-empty, and equal (constant-time compare).
* Mismatch / missing → 403 with ``error.code == csrf_failure`` and
  a concrete message.

Tests in ``test_csrf_middleware.py`` pin this contract.
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


_MUTATING = {"POST", "PUT", "DELETE", "PATCH"}
_ADMIN_PREFIX = "/admin/"
_EXEMPT_PREFIXES: tuple[str, ...] = (
    # The session endpoints set the CSRF cookie on first login — a
    # caller can't have one yet. These paths live under /auth/session
    # which already isn't /admin/*, but we list them explicitly here
    # as a belt-and-braces hint for future refactors.
    "/auth/session/login",
    "/auth/session/logout",
)

# The main test conftest flips this flag for the shared TestClient on
# ``app.main.app`` so the existing admin-route integration tests don't
# need to be rewritten to send CSRF headers. Unit tests that target
# the middleware itself build their own FastAPI app and DO NOT flip
# the flag, so the enforcement paths stay covered.
_BYPASS_FOR_TESTING: bool = False


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _BYPASS_FOR_TESTING:
            return await call_next(request)
        path = request.url.path
        method = request.method.upper()
        if method not in _MUTATING:
            return await call_next(request)
        if not path.lower().startswith(_ADMIN_PREFIX):
            return await call_next(request)
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        cookie_val = request.cookies.get("csrf_token", "")
        header_val = request.headers.get("x-csrf-token", "")

        if not cookie_val or not header_val:
            return _csrf_fail("CSRF cookie or header missing.")
        if not hmac.compare_digest(cookie_val, header_val):
            return _csrf_fail("CSRF cookie/header mismatch.")

        return await call_next(request)


def _csrf_fail(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": "csrf_failure",
                "message": message,
            }
        },
    )
