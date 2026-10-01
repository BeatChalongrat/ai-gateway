"""The first admin of a deployment — configuration, never a constant.

Every path that can create the first admin-ui account (the startup seed in
``app.main``, ``make seed``, the UAT seed and ``python -m app.ops.first_admin``)
reads the account from here, so they cannot disagree:

* ``INITIAL_ADMIN_EMAIL`` — the deployment's own address. There is no default:
  without it no account is created, in any environment. A reserved placeholder
  domain (``example.com`` / ``.org`` / ``.net``) is refused, so a fresh install
  can never come up with a demo-looking admin.
* ``INITIAL_ADMIN_USERNAME`` — optional; the e-mail when unset.
* ``INITIAL_ADMIN_PASSWORD`` — required outside ``APP_ENV=development``; in
  development the documented dev password is used when unset.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

DEV_PASSWORD = "admin1234"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# RFC 2606 documentation domains: fine in a manual, never a real admin.
_PLACEHOLDER_DOMAINS = frozenset({"example.com", "example.org", "example.net"})


class FirstAdminNotConfigured(RuntimeError):
    """The environment does not name a usable first admin; the message says what to set."""


@dataclass(frozen=True)
class FirstAdmin:
    username: str
    email: str
    password: str


def first_admin_from_env(env: Mapping[str, str] | None = None) -> FirstAdmin:
    """The first admin this deployment configured, or ``FirstAdminNotConfigured``."""
    env = os.environ if env is None else env
    email = (env.get("INITIAL_ADMIN_EMAIL") or "").strip().lower()
    if not email:
        raise FirstAdminNotConfigured(
            "INITIAL_ADMIN_EMAIL is not set. Set it to this deployment's own admin address in .env.")
    if not _EMAIL.match(email):
        raise FirstAdminNotConfigured("INITIAL_ADMIN_EMAIL is not an e-mail address.")
    if email.rsplit("@", 1)[1] in _PLACEHOLDER_DOMAINS:
        raise FirstAdminNotConfigured(
            "INITIAL_ADMIN_EMAIL is a placeholder address (example.*). Set this deployment's own admin address.")
    password = env.get("INITIAL_ADMIN_PASSWORD") or ""
    if not password:
        if (env.get("APP_ENV") or "development").lower() != "development":
            raise FirstAdminNotConfigured(
                "INITIAL_ADMIN_PASSWORD is required when APP_ENV != 'development'. "
                "Generate one with `openssl rand -base64 24` and set it in .env.")
        password = DEV_PASSWORD
    username = (env.get("INITIAL_ADMIN_USERNAME") or "").strip() or email
    return FirstAdmin(username=username, email=email, password=password)
