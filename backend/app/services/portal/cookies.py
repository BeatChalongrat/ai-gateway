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
