"""Portal auth cookie helpers — HttpOnly cookie migration.

Single source of truth for how portal access / refresh tokens are
written to and cleared from the response. Login, refresh, and logout
all go through here so the attributes (HttpOnly / Secure / SameSite /
Path / Domain / Max-Age) can never drift between endpoints.

Why HttpOnly cookies instead of returning tokens in the JSON body:
the previous design handed the JWT to the browser, which stored it in
localStorage + a JS-written cookie — readable by any script, so any XSS
exfiltrated the session. Setting the cookies server-side with HttpOnly
closes that hole; JS never sees the token.

Path:
  * Access cookie — ``/`` so it rides every request, including the
    cross-origin calls the web-chat app makes to chat-edge (:4000) and
    the gateway (:8080).
  * Refresh cookie — also ``/``. The spec floated a narrow
    ``/api/portal/auth`` path to limit exposure, but the portal reaches
    the refresh endpoint through its Next proxy (``/api/portal/auth/...``)
    while web-chat reaches it through a different path, and a single Path
    can't match both. The cookie is HttpOnly + Secure + SameSite=Lax, so
    the narrow-path hardening would be redundant anyway. We keep ``/`` so
    refresh works identically from either app.

Domain / Secure / SameSite come from settings (see config.py) so a
production deploy flips Secure on and can set a shared parent domain
without code changes.

CSRF posture: the portal/chat session relies on ``SameSite=Lax`` rather
than the double-submit token the admin domain uses. Lax withholds the
cookie on cross-SITE state-changing requests (programmatic POST/PUT/
DELETE from another registrable domain), which is the CSRF vector. The
same-site cross-port calls in this stack (portal/chat -> gateway) are
trusted and DO carry the cookie. A dedicated double-submit token would be
strictly additive; it's intentionally omitted here to keep the portal
flow simple, with Lax as the load-bearing defence. If portal and api ever
move to a cross-SITE topology (different registrable domains, requiring
``SameSite=None``), a CSRF token MUST be reintroduced.

DEFERRED — portal double-submit CSRF (tracked in docs/DEFERRED_WORK.md).
Extending the admin double-submit CSRF (app/middleware/csrf.py) to
``/portal/*`` mutations is a deliberate, first-class deferral — NOT an
oversight and NOT an open hole. Current posture:

  * The CSRF vector is ALREADY blocked. Portal/chat session cookies are
    ``SameSite=Lax`` (see ``_common_kwargs``), which withholds the cookie
    on cross-SITE state-changing requests — the exact CSRF vector.
  * There is no silent-exposure window. ``config.validate_secrets()``
    FAIL-CLOSES production startup if ``AUTH_COOKIE_SAMESITE=none`` is set
    without ``AUTH_COOKIE_DOMAIN`` + Secure, so Lax can't be voided by a
    misconfigured deploy without the process refusing to boot.

Adding a portal double-submit token is therefore DEFENCE-IN-DEPTH
CONSISTENCY with the admin domain, not closing an open hole. It is
deferred because it CANNOT ship as a portal-only change — it touches four
clients and must land ATOMICALLY or it breaks installs in the field:

  * The SRAN Desktop WPF client (AgentKeyProvider.cs) calls mutating
    portal routes (``/portal/projects``, ``/portal/projects/*/keys``)
    authenticated by ``Authorization: Bearer <portal-JWT>`` (AuthTokenHandler),
    with no CSRF header. ``get_portal_user`` accepts cookie-first then
    Bearer, so desktop is a Bearer caller — a naive middleware that
    enforced on all ``/portal/*`` mutations would 403 every desktop install.
  * web-chat (:3002) calls the gateway's ``/portal/auth/refresh`` +
    ``/portal/auth/logout`` cross-origin; a JS-readable csrf_token
    cookie scoped to the gateway host is not readable from the web-chat
    origin unless AUTH_COOKIE_DOMAIN-style scoping is added for it too.

ATOMIC-ROLLOUT CHECKLIST (all five land in ONE change + a desktop
regression test, else desktop installs 403):
  (a) mint a JS-readable ``portal_csrf`` cookie in ``set_auth_cookies``
      (same Domain/Secure attrs, ``httponly=False``);
  (b) widen ``CsrfMiddleware`` to ``/portal/`` mutations, exempting
      ``/portal/auth/*`` (bootstrap — no cookie yet) AND requests
      authenticated by non-cookie means (``Authorization: Bearer`` /
      internal-trust headers) so desktop + service callers keep working;
  (c) echo the cookie as ``X-CSRF-Token`` in portal/lib/api.ts;
  (d) echo it in apps/web-chat/src/lib/api.ts (+ lib/auth.ts);
  (e) echo it in the DESKTOP client (AgentKeyProvider / the portal HTTP
      path) — or keep desktop on the Bearer exemption from (b) — plus a
      desktop regression test proving it still authenticates.

Until then Lax remains the load-bearing defence and the
``config.validate_secrets()`` startup guard blocks any SameSite=None
deploy that would silently void it. Do NOT partially enable any of
(a)-(e) on its own — a half-ship either does nothing (no enforcement) or
403s live clients (enforcement without the echo).
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import settings


ACCESS_COOKIE = "portal_access_token"
REFRESH_COOKIE = "portal_refresh_token"

# Paths — see module docstring.
_ACCESS_PATH = "/"
_REFRESH_PATH = "/"


def _common_kwargs() -> dict:
    """Shared cookie attributes resolved from settings at call time."""
    kwargs: dict = {
        "httponly": True,
        "secure": settings.cookie_secure_effective,
        "samesite": settings.auth_cookie_samesite,
    }
    # Only attach Domain when explicitly configured. An empty value must
    # NOT be passed through as ``domain=""`` — that is not the same as a
    # host-only cookie in every client. Omit the kwarg entirely instead.
    if settings.auth_cookie_domain:
        kwargs["domain"] = settings.auth_cookie_domain
    return kwargs


def set_auth_cookies(
    response: Response, *, access_token: str, refresh_token: str
) -> None:
    """Write both auth cookies on ``response`` (login + refresh)."""
    common = _common_kwargs()
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.portal_access_token_expire_minutes * 60,
        path=_ACCESS_PATH,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.portal_refresh_token_expire_days * 24 * 3600,
        path=_REFRESH_PATH,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    """Expire both auth cookies (logout + refresh-failure).

    ``delete_cookie`` must echo the same Path/Domain/SameSite/Secure the
    cookie was set with or the browser keeps the original.
    """
    common = _common_kwargs()
    # delete_cookie does not take httponly; drop it.
    common.pop("httponly", None)
    response.delete_cookie(ACCESS_COOKIE, path=_ACCESS_PATH, **common)
    response.delete_cookie(REFRESH_COOKIE, path=_REFRESH_PATH, **common)
