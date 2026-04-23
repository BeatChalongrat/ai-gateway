"""Email template rendering.

Templates live alongside this file as paired ``<name>.<lang>.html`` +
``<name>.<lang>.txt`` under ``app/services/email/templates/``.
Rendering is trivially substitution-based — a full Jinja/Mako stack
is overkill for three messages. Using ``str.replace`` keeps the
templates greppable and obvious.

Language selection: the caller passes ``language="en"`` or
``language="th"``. Unknown / missing language files fall back to
English silently — a Thai user with a missing translation still
receives the message instead of a 500 during signup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_SUPPORTED_LANGS = ("en", "th")


def _load(name: str, language: str, ext: str) -> str:
    """Read ``templates/<name>.<lang>.<ext>`` with English fallback.

    Caches nothing — these are small files, called a handful of times
    per minute at internal-tool scale, and caching just adds a file-
    watch gotcha for hot-reload in dev.
    """
    lang = language if language in _SUPPORTED_LANGS else "en"
    candidate = _TEMPLATE_DIR / f"{name}.{lang}.{ext}"
    if not candidate.exists():
        candidate = _TEMPLATE_DIR / f"{name}.en.{ext}"
    return candidate.read_text(encoding="utf-8")


def _substitute(body: str, vars_: dict[str, str]) -> str:
    """Replace ``{{key}}`` placeholders. Missing keys stay literal so
    a template typo is visible at render time instead of silently
    blanking a field."""
    for key, value in vars_.items():
        body = body.replace("{{" + key + "}}", value)
    return body


def _subject_line(body: str) -> tuple[str, str]:
    """Pull the first line as the Subject: and return (subject, body).

    Template convention: the very first line is the subject (no
    ``Subject:`` prefix), a blank line separates it from the body.
    Keeps subjects translatable alongside their message without a
    sidecar config file.
    """
    first, _, rest = body.partition("\n\n")
    return first.strip(), rest.strip()


def render_verification_email(
    *,
    display_name: Optional[str],
    verification_link: str,
    language: str = "en",
) -> tuple[str, str, str]:
    vars_ = {
        "display_name": (display_name or "there"),
        "verification_link": verification_link,
    }
    text = _substitute(_load("verification", language, "txt"), vars_)
    subject, text_body = _subject_line(text)
    html = _substitute(_load("verification", language, "html"), vars_)
    # HTML templates start with the body; subject comes from the txt
    # partner so translators only maintain one subject per language.
    return subject, html, text_body


def render_password_reset_email(
    *,
    display_name: Optional[str],
    reset_link: str,
    expires_in_minutes: int,
    language: str = "en",
) -> tuple[str, str, str]:
    vars_ = {
        "display_name": (display_name or "there"),
        "reset_link": reset_link,
        "expires_in_minutes": str(expires_in_minutes),
    }
    text = _substitute(_load("password_reset", language, "txt"), vars_)
    subject, text_body = _subject_line(text)
    html = _substitute(_load("password_reset", language, "html"), vars_)
    return subject, html, text_body


def render_invite_email(
    *,
    inviter_name: Optional[str],
    tenant_name: str,
    invite_link: str,
    invite_code: str,
    language: str = "en",
) -> tuple[str, str, str]:
    vars_ = {
        "inviter_name": (inviter_name or "A teammate"),
        "tenant_name": tenant_name,
        "invite_link": invite_link,
        "invite_code": invite_code,
    }
    text = _substitute(_load("invite", language, "txt"), vars_)
    subject, text_body = _subject_line(text)
    html = _substitute(_load("invite", language, "html"), vars_)
    return subject, html, text_body
