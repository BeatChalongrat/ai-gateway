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
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog

from app.core.config import settings
from app.services.alert_scrub import scrub_text, scrub_value

logger = structlog.get_logger(__name__)

_TIMEOUT_S = 6.0


# FIX130 E4 — an alert URL is a credential: an ntfy.sh topic IS the secret
# of that channel (anyone who knows it reads every alert), and a webhook's
# path often is too (Slack-style /services/T…/B…/<secret>). httpx logs every
# request at INFO ("HTTP Request: POST <url> …") and the backend's root
# logger is at INFO, so the first delivered alert would have written the
# topic into the backend log. This filter on the "httpx" logger replaces the
# configured channel URLs, and every rule webhook URL WebhookService posts
# to, with scheme://host/… before the record is formatted anywhere.
_SECRET_URLS: set[str] = set()


def url_label(url: str) -> str:
    """``https://ntfy.sh/<topic>`` → ``https://ntfy.sh/…`` (safe to log)."""
    try:
        u = httpx.URL(url)
        return f"{u.scheme}://{u.host}{f':{u.port}' if u.port else ''}/…"
    except Exception:  # noqa: BLE001
        return "<alert url>"


def register_secret_url(url: str | None) -> None:
    """Keep ``url`` out of the httpx request log (see ``_HideAlertUrls``)."""
    if url and url.strip():
        _SECRET_URLS.add(url.strip())


class _HideAlertUrls(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        urls = {u for u in ((settings.alert_ntfy_url or "").strip(),
                            (settings.alert_webhook_url or "").strip(),
                            (getattr(settings, "alert_test_ntfy_url", "") or "").strip(),
                            (getattr(settings, "agent_market_ntfy_url", "") or "").strip()) if u} | _SECRET_URLS
        if not urls:
            return True
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — never break logging
            return True
        hit = False
        for u in sorted(urls, key=len, reverse=True):
            if u in msg:
                msg = msg.replace(u, url_label(u))
                hit = True
        if hit:
            record.msg, record.args = msg, ()
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, _HideAlertUrls) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(_HideAlertUrls())

# ntfy priority scale: 1 min … 5 max/urgent. FIX131: "low" (2 — no sound,
# no vibration) for "recovered" notes; "critical" (5) is only for "users
# cannot use the system" (alert_pager / alert_catalog decide which).
_NTFY_PRIORITY = {"low": "2", "info": "3", "warning": "4", "critical": "5"}

# FIX131 D4 — environment names whose alerts are tests: labelled [TEST] and
# routed to ALERT_TEST_NTFY_URL only. Real names carry no label.
TEST_ENVIRONMENTS = frozenset({"test", "testing", "ci", "lane", "test-stack"})
_REAL_ENVIRONMENTS = frozenset({"", "prod", "production", "live"})


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
    severity: str = "warning"  # low | info | warning | critical
    source: str = "backend"
    tags: tuple[str, ...] = field(default_factory=tuple)
    details: dict = field(default_factory=dict)
    # FIX131 D4 — a test fire: labelled [TEST] and sent to the test topic
    # only, whatever the environment.
    test: bool = False

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
            **({"test": True} if self.test else {}),
        }


def environment_name() -> str:
    return (getattr(settings, "alert_environment", "") or "").strip().lower()


def is_test_environment() -> bool:
    return environment_name() in TEST_ENVIRONMENTS


def environment_label(test: bool = False) -> str:
    """The prefix every alert of this deployment carries: ``[TEST]`` for a
    test environment or a test fire, ``[STAGING]`` etc. for another named
    one, nothing for the real system."""
    env = environment_name()
    if test or env in TEST_ENVIRONMENTS:
        return "[TEST]"
    if env in _REAL_ENVIRONMENTS:
        return ""
    return f"[{env.upper()}]"


def labelled(n: Notification) -> Notification:
    """``n`` with the environment label on its title AND its body."""
    label = environment_label(n.test)
    if not label:
        return n

    def _l(text: str) -> str:
        return text if text.startswith(label) else f"{label} {text}"

    return dataclasses.replace(n, title=_l(n.title), message=_l(n.message))


def configured_channels(test: bool = False) -> list[str]:
    """Where an alert goes. A test (``test=True`` or a test environment)
    goes to ``ntfy-test`` (ALERT_TEST_NTFY_URL) or nowhere — never to the
    owner's channels."""
    if test or is_test_environment():
        return ["ntfy-test"] if (getattr(settings, "alert_test_ntfy_url", "") or "").strip() else []
    out = []
    if (settings.alert_ntfy_url or "").strip():
        out.append("ntfy")
    # FIX134 C3 — email only when mail can actually leave this process:
    # ALERT_EMAIL_TO with EMAIL_DELIVERY=disabled (or no Resend key) used to
    # make every alert log a Resend failure and a skipped retry.
    if (settings.alert_email_to or "").strip() and email_channel_off_reason() is None:
        out.append("email")
    if (settings.alert_webhook_url or "").strip():
        out.append("webhook")
    return out


def email_channel_off_reason() -> str | None:
    """FIX134 C3 — why ALERT_EMAIL_TO cannot be honoured right now
    (``email_delivery_disabled`` / ``no_resend_api_key``), or None when the
    sender can deliver. The one definition lives with the sender."""
    from app.services.email.sender import delivery_unavailable_reason

    return delivery_unavailable_reason()


def _header_value(text: str) -> str:
    """HTTP headers are ASCII; ntfy reads RFC 2047 for a Thai title."""
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        import base64

        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def _send_ntfy(n: Notification, client: httpx.Client, *, test: bool = False, url: str | None = None) -> bool:
    # FIX131 D4 — the test topic never gets the owner's token; nor does a
    # topic passed in (AM1c: Agent Market's own).
    token = "" if (test or url) else (settings.alert_ntfy_token or "").strip()
    url = (url or (settings.alert_test_ntfy_url if test else settings.alert_ntfy_url)).strip()
    # ntfy renders a known tag as an emoji (rotating_light, warning); the
    # severity word stays so the notification list is searchable by it.
    tags = (["rotating_light", "critical"] if n.severity == "critical" else [n.severity]) + list(n.tags)
    if test:
        tags.append("test")
    headers = {
        "Title": _header_value(n.title),
        "Priority": _NTFY_PRIORITY.get(n.severity, "3"),
        "Tags": ",".join(tags),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
    """Send ``n`` on every configured channel. Returns {channel: ok}.

    FIX131 D4 — labelled with the environment ([TEST] …) and, for a test
    (a test fire or a test environment), sent to ALERT_TEST_NTFY_URL only."""
    test = n.test or is_test_environment()
    n = labelled(scrubbed(n))
    outcomes: dict[str, bool] = {}
    channels = configured_channels(test=test)
    if not channels:
        logger.warning(
            "alert_no_channel_configured", title=n.title, severity=n.severity, test=test
        )
        return outcomes
    with httpx.Client(timeout=_TIMEOUT_S) as client:
        for ch in channels:
            try:
                if ch == "ntfy-test":
                    ok = _send_ntfy(n, client, test=True)
                elif ch == "ntfy":
                    ok = _send_ntfy(n, client)
                elif ch == "email":
                    ok = _send_email(n)
                else:
                    ok = _send_webhook(n, client)
            except Exception as exc:  # noqa: BLE001 — delivery never raises
                err = str(exc)
                for u in (settings.alert_ntfy_url, settings.alert_webhook_url,
                          getattr(settings, "alert_test_ntfy_url", "")):
                    if (u or "").strip():
                        err = err.replace(u.strip(), url_label(u.strip()))
                logger.warning(
                    "alert_delivery_failed", channel=ch, title=n.title,
                    error=f"{type(exc).__name__}: {err[:160]}",
                )
                ok = False
            outcomes[ch] = ok
    logger.info("alert_delivered", title=n.title, severity=n.severity, outcomes=outcomes)
    return outcomes


def deliver_agent_market(n: Notification) -> dict[str, bool]:
    """AM1c — an Agent Market alert: to AGENT_MARKET_NTFY_URL only, never the
    core channels, so it is seen without paging anyone for a core incident.
    Labelled like every alert; in a test environment it goes to
    ALERT_TEST_NTFY_URL instead (a test fire stays on the agent topic)."""
    test_env = is_test_environment()
    n = labelled(scrubbed(n))
    url = (getattr(settings, "alert_test_ntfy_url", "") if test_env
           else getattr(settings, "agent_market_ntfy_url", "")) or ""
    channel = "ntfy-test" if test_env else "ntfy-agent-market"
    if not url.strip():
        logger.warning("alert_no_channel_configured", title=n.title, severity=n.severity, channel=channel)
        return {}
    tagged = dataclasses.replace(n, tags=tuple(n.tags) + ("agent-market",))
    try:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            ok = _send_ntfy(tagged, client, test=test_env, url=url.strip())
    except Exception as exc:  # noqa: BLE001 — delivery never raises
        logger.warning("alert_delivery_failed", channel=channel, title=n.title,
                       error=f"{type(exc).__name__}: {str(exc).replace(url.strip(), url_label(url.strip()))[:160]}")
        ok = False
    logger.info("alert_delivered", title=n.title, severity=n.severity, outcomes={channel: ok})
    return {channel: ok}


async def deliver_async(n: Notification) -> dict[str, bool]:
    return await asyncio.to_thread(deliver, n)
