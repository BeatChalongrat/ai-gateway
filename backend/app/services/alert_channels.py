"""FIX129 A4 — where an alert goes so a person actually sees it.

Before this, an alert rule could only POST to its own ``webhook_url`` (and
``alert_rules`` was empty), so the 34-minute outage of 2026-09-22 told
nobody. This module is the one delivery point used by the rule evaluator
(``alert_service.emit_and_deliver``), the backup job and the ops watcher:

* **ntfy** (``ALERT_NTFY_URL`` = a topic URL such as ``https://ntfy.sh/<topic>``):
  a push notification on the owner's phone with no account — the ntfy app
  subscribes to the topic. ``ALERT_NTFY_TOKEN`` for a protected topic.
* **email** (``ALERT_EMAIL_TO``): through the existing Resend sender. In a
  development ``APP_ENV`` that sender only logs, so email is a production
  channel; ntfy works everywhere.
* **webhook** (``ALERT_WEBHOOK_URL``): the alert payload as JSON.

Delivery is synchronous (``httpx.Client``) so the ops watcher — a plain
process, no event loop — can use it; ``deliver_async`` runs it in a thread.
It never raises and never puts a secret in a message: every notification is
passed through ``alert_scrub`` first (FIX130 E4 — no SQL, credentials,
tokens or user text leave in an alert, whatever the caller built).
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog

from app.core.config import settings
from app.services.alert_scrub import scrub_text, scrub_value

logger = structlog.get_logger(__name__)

_TIMEOUT_S = 6.0

# ntfy priority scale: 1 min … 5 max/urgent.
_NTFY_PRIORITY = {"info": "3", "warning": "4", "critical": "5"}


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
    severity: str = "warning"  # info | warning | critical
    source: str = "backend"
    tags: tuple[str, ...] = field(default_factory=tuple)
    details: dict = field(default_factory=dict)

    def payload(self) -> dict:
        return {
            "event": "alert",
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "source": self.source,
            "tags": list(self.tags),
            "details": self.details,
            "fired_at": datetime.now(timezone.utc).isoformat(),
        }


def configured_channels() -> list[str]:
    out = []
    if (settings.alert_ntfy_url or "").strip():
        out.append("ntfy")
    if (settings.alert_email_to or "").strip():
        out.append("email")
    if (settings.alert_webhook_url or "").strip():
        out.append("webhook")
    return out


def _header_value(text: str) -> str:
    """HTTP headers are ASCII; ntfy reads RFC 2047 for a Thai title."""
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        import base64

        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def _send_ntfy(n: Notification, client: httpx.Client) -> bool:
    url = settings.alert_ntfy_url.strip()
    # ntfy renders a known tag as an emoji (rotating_light, warning); the
    # severity word stays so the notification list is searchable by it.
    tags = (["rotating_light", "critical"] if n.severity == "critical" else [n.severity]) + list(n.tags)
    headers = {
        "Title": _header_value(n.title),
        "Priority": _NTFY_PRIORITY.get(n.severity, "3"),
        "Tags": ",".join(tags),
    }
    if (settings.alert_ntfy_token or "").strip():
        headers["Authorization"] = f"Bearer {settings.alert_ntfy_token.strip()}"
    r = client.post(url, content=n.message.encode("utf-8"), headers=headers)
    return 200 <= r.status_code < 300


def _send_email(n: Notification) -> bool:
    from app.services.email.sender import send_email

    subject = f"[SRAN alert] {n.severity.upper()}: {n.title}"
    lines = [n.message, "", f"source: {n.source}", f"severity: {n.severity}"]
    for k, v in n.details.items():
        lines.append(f"{k}: {v}")
    text_body = "\n".join(lines)
    html_body = "<pre>" + (
        text_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ) + "</pre>"
    res = send_email(
        to=settings.alert_email_to.strip(),
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    return bool(res.delivered)


def _send_webhook(n: Notification, client: httpx.Client) -> bool:
    r = client.post(settings.alert_webhook_url.strip(), json=n.payload())
    return 200 <= r.status_code < 300


def scrubbed(n: Notification) -> Notification:
    """FIX130 E4 — ``n`` as it may leave the machine: title, message and
    details without SQL, credentials, tokens or user content. The last hop,
    so a caller that forgets (backend rule, ops watcher) cannot leak."""
    return dataclasses.replace(
        n, title=scrub_text(n.title), message=scrub_text(n.message), details=scrub_value(n.details)
    )


def deliver(n: Notification) -> dict[str, bool]:
    """Send ``n`` on every configured channel. Returns {channel: ok}."""
    n = scrubbed(n)
    outcomes: dict[str, bool] = {}
    channels = configured_channels()
    if not channels:
        logger.warning(
            "alert_no_channel_configured", title=n.title, severity=n.severity
        )
        return outcomes
    with httpx.Client(timeout=_TIMEOUT_S) as client:
        for ch in channels:
            try:
                if ch == "ntfy":
                    ok = _send_ntfy(n, client)
                elif ch == "email":
                    ok = _send_email(n)
                else:
                    ok = _send_webhook(n, client)
            except Exception as exc:  # noqa: BLE001 — delivery never raises
                logger.warning(
                    "alert_delivery_failed", channel=ch, title=n.title,
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
                ok = False
            outcomes[ch] = ok
    logger.info("alert_delivered", title=n.title, severity=n.severity, outcomes=outcomes)
    return outcomes


async def deliver_async(n: Notification) -> dict[str, bool]:
    return await asyncio.to_thread(deliver, n)
