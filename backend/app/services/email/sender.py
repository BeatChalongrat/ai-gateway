"""Transactional email dispatch (Phase 3.5 — Resend).

Design principles (from Beat's locked Phase 3.5 spec):

* **Fail-open on signup**: Resend outage must not block a user from
  signing up. ``send_email`` logs the failure and returns a
  ``EmailSendResult`` with ``delivered=False`` so the caller can
  decide whether to surface anything — but should NOT raise.
* **Dev-mode is console-only**: when ``APP_ENV`` is development or
  test, we never call the Resend API. The payload is written to the
  structured logger so integration tests can assert on it and local
  developers can copy the verification link out of their terminal.
* **Retries happen in a background task**. ``send_email`` dispatches
  a one-shot attempt; failures schedule a retry (three attempts at
  2s / 4s / 8s backoff) in the FastAPI background-task pool. If
  every retry fails, we log and drop — no queue persistence until
  bounce handling lands post-launch.
* **No HTML-only templates**: every call MUST supply both
  ``html_body`` and ``text_body``. This is a Resend recommendation
  and keeps the code honest about plaintext fallback for mail
  clients that strip HTML.

Higher-level wrappers (``send_verification_email`` etc.) live in
this module for discoverability — the template loader is separate
(``templates.py``) so unit tests can substitute fixtures without
touching the send path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings


_log = logging.getLogger("email.sender")

_DEV_ENVS = {"development", "test", "testing", "dev"}

# Retry schedule when the real Resend call fails. Short enough to
# cover a transient DNS blip without delaying the worker for long.
# Persistence across process restarts is NOT in scope — documented
# deferral for post-launch.
_RETRY_DELAYS_SECONDS = (2, 4, 8)


@dataclass
class EmailSendResult:
    """Shape returned by every send_* call.

    ``delivered`` is True only when Resend (or the dev-mode console
    logger) accepted the payload. ``reason`` is a short human-readable
    tag — callers surface it in logs / audit rows, never directly to
    an end user.
    """

    delivered: bool
    provider: str
    reason: Optional[str] = None
    message_id: Optional[str] = None


def _is_dev_env() -> bool:
    return settings.app_env.lower() in _DEV_ENVS


def _console_log_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> EmailSendResult:
    """Dev-mode sender: structured log only, never hits the network.

    The full payload (including tokens inside URLs) is intentionally
    logged so local developers and integration tests can recover the
    link without a mail server. This is fine for dev — see the
    ``APP_ENV`` gate — but is exactly why production must use Resend.
    """
    _log.info(
        "email.dev_console",
        extra={
            "email_to": to,
            "email_subject": subject,
            "email_text_body": text_body,
            # HTML is verbose; don't pollute logs unless someone asks.
            # Structured loggers still capture the field for tests that
            # want to assert on it.
            "email_html_body_length": len(html_body),
        },
    )
    return EmailSendResult(delivered=True, provider="console", reason="dev_mode")


def _send_via_resend(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> EmailSendResult:
    """One-shot call to Resend. Raises nothing — returns the result.

    Import is lazy so a dev box without ``resend`` installed still
    boots (APP_ENV=development takes the console path before we get
    here).
    """
    try:
        import resend  # type: ignore[import-not-found]

        resend.api_key = settings.resend_api_key
        from_header = (
            f"{settings.email_from_name} <{settings.email_from_address}>"
            if settings.email_from_name
            else settings.email_from_address
        )
        response = resend.Emails.send(
            {
                "from": from_header,
                "to": [to],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            }
        )
        message_id = (response or {}).get("id") if isinstance(response, dict) else None
        return EmailSendResult(
            delivered=True,
            provider="resend",
            message_id=message_id,
        )
    except Exception as exc:  # pragma: no cover — exercised via mocked tests
        # Swallow the exception intentionally. The caller uses
        # ``delivered`` to decide whether to schedule a retry; we do
        # not want a Resend outage to surface as a 500 to the user.
        _log.warning(
            "email.resend_failure",
            extra={
                "email_to": to,
                "email_subject": subject,
                "email_error": str(exc)[:200],
            },
        )
        return EmailSendResult(
            delivered=False,
            provider="resend",
            reason=f"send_failed: {type(exc).__name__}",
        )


async def _retry_in_background(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> EmailSendResult:
    """Retry a failed send ``len(_RETRY_DELAYS_SECONDS)`` times.

    Called from ``send_email`` only when the initial attempt fails
    AND we're in production mode. Async so we can be fired-and-
    forgotten via ``asyncio.create_task``.
    """
    last = EmailSendResult(delivered=False, provider="resend", reason="retry_pending")
    for attempt, delay in enumerate(_RETRY_DELAYS_SECONDS, start=1):
        await asyncio.sleep(delay)
        last = _send_via_resend(
            to=to, subject=subject, html_body=html_body, text_body=text_body
        )
        if last.delivered:
            _log.info(
                "email.retry_succeeded",
                extra={"email_to": to, "attempt": attempt},
            )
            return last
    _log.error(
        "email.retries_exhausted",
        extra={"email_to": to, "email_subject": subject},
    )
    return last


def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> EmailSendResult:
    """Dispatch a single transactional email.

    Returns synchronously; the retry task (if any) is scheduled on
    the running event loop and we do NOT await it. Callers can
    inspect the returned ``EmailSendResult`` to decide what to log,
    but should not block the user-facing response on its
    ``delivered`` flag — per spec, signup / reset must succeed even
    when email is down.
    """
    if not to or "@" not in to:
        return EmailSendResult(
            delivered=False, provider="noop", reason="invalid_recipient"
        )

    delivery = (getattr(settings, "email_delivery", "auto") or "auto").strip().lower()
    if delivery == "disabled":
        # FIX133 B — a deployment that states it has no mail sender: the
        # caller learns the mail was NOT delivered (and logs it); nothing
        # pretends to have sent it.
        _log.warning(
            "email.delivery_disabled",
            extra={"email_to": to, "email_subject": subject},
        )
        return EmailSendResult(delivered=False, provider="disabled", reason="email_delivery_disabled")
    if delivery == "console" or (delivery == "auto" and _is_dev_env()):
        return _console_log_email(
            to=to, subject=subject, html_body=html_body, text_body=text_body
        )

    result = _send_via_resend(
        to=to, subject=subject, html_body=html_body, text_body=text_body
    )
    if not result.delivered:
        # Fire-and-forget retry. If we're not inside an event loop
        # (e.g. called from a sync admin script) the retry simply
        # doesn't happen — better than crashing.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                _retry_in_background(
                    to=to,
                    subject=subject,
                    html_body=html_body,
                    text_body=text_body,
                )
            )
        except RuntimeError:
            _log.warning(
                "email.retry_skipped_no_loop",
                extra={"email_to": to, "email_subject": subject},
            )
    return result


# ---------------------------------------------------------------------------
# High-level wrappers — thin layers over send_email that delegate to
# the template module for body rendering. Defined in this file so
# callers only ever import from `app.services.email`.
# ---------------------------------------------------------------------------


def send_verification_email(
    *,
    to: str,
    display_name: Optional[str],
    verification_link: str,
    language: str = "en",
) -> EmailSendResult:
    """Verification email — called from portal_auth.request_verification_email."""
    from app.services.email.templates import render_verification_email

    subject, html, text = render_verification_email(
        display_name=display_name,
        verification_link=verification_link,
        language=language,
    )
    return send_email(to=to, subject=subject, html_body=html, text_body=text)


def send_password_reset_email(
    *,
    to: str,
    display_name: Optional[str],
    reset_link: str,
    expires_in_minutes: int,
    language: str = "en",
) -> EmailSendResult:
    """Password-reset email — called from the /password-reset/request route."""
    from app.services.email.templates import render_password_reset_email

    subject, html, text = render_password_reset_email(
        display_name=display_name,
        reset_link=reset_link,
        expires_in_minutes=expires_in_minutes,
        language=language,
    )
    return send_email(to=to, subject=subject, html_body=html, text_body=text)


def send_invite_email(
    *,
    to: str,
    inviter_name: Optional[str],
    tenant_name: str,
    invite_link: str,
    invite_code: str,
    language: str = "en",
) -> EmailSendResult:
    """Team invite email — OPTIONAL path.

    Phase 3's invite flow is copy-paste codes, not email. This helper
    is wired up so a future UI flip (generate-and-email instead of
    generate-and-copy) doesn't need another round of plumbing.
    """
    from app.services.email.templates import render_invite_email

    subject, html, text = render_invite_email(
        inviter_name=inviter_name,
        tenant_name=tenant_name,
        invite_link=invite_link,
        invite_code=invite_code,
        language=language,
    )
    return send_email(to=to, subject=subject, html_body=html, text_body=text)
