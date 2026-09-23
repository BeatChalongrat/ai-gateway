"""Alert/notification foundation for enterprise events.

Emit an ``Alert`` to:
  - structured log (always),
  - ``alert_events`` table for audit trail (when a DB session is given),
  - an external webhook via ``WebhookService`` (when a URL is given).

Design note on the two emit paths
---------------------------------
``emit()`` is synchronous — legacy callers (``budget_warning`` /
``sensitive_action``) are sync and their tests call them without an
event loop, so the hot path must stay sync-compatible. Webhook
delivery is async, so the full fire-and-deliver flow lives in
``emit_and_deliver()``, which is the entry point used by
``AlertEvaluator``.

A call that includes a ``webhook_url`` but uses the sync ``emit()``
path writes the event row with ``delivery_status='pending'`` so an
operator can tell that the webhook was requested but never actually
dispatched (there's no sync path to call httpx). The async path
updates the status to ``delivered`` / ``failed`` after the HTTP call.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

import structlog

from app.models.alert_rule import AlertEventORM
from app.services.alert_scrub import scrub_text, scrub_value
from app.services.webhook_service import DeliveryStatus, WebhookService

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    BUDGET_THRESHOLD = "budget_threshold"
    QUOTA_EXHAUSTED = "quota_exhausted"
    PROVIDER_FAILURE = "provider_failure"
    DLP_BLOCK_SPIKE = "dlp_block_spike"
    # FIX120 — the primary keeps failing but the fallback keeps answering,
    # so nobody complains: the count of rescued turns is the only alarm.
    FALLBACK_SPIKE = "fallback_spike"
    # FIX129 A4 — the three baseline rules (alert_defaults.py).
    NO_SUCCESSFUL_TURN = "no_successful_turn"
    ERROR_RATE = "error_rate"
    STUCK_TRANSACTION = "stuck_transaction"
    KEY_REVOKED = "key_revoked"
    SENSITIVE_ACTION = "sensitive_action"


@dataclass(frozen=True)
class Alert:
    alert_type: AlertType
    severity: AlertSeverity
    tenant_id: str
    project_id: str = ""
    message: str = ""
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertService:
    """Emit structured alerts, persist an audit row, optionally webhook."""

    def __init__(self, webhook_service: WebhookService | None = None) -> None:
        self._webhook_service = webhook_service or WebhookService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(
        self,
        alert: Alert,
        session: "Session | None" = None,
        webhook_url: str | None = None,
        rule_id: str | None = None,
    ) -> AlertEventORM | None:
        """Log the alert, persist it if a session is provided.

        Synchronous path — no webhook call. Use ``emit_and_deliver``
        when a webhook needs to actually be dispatched.
        """
        alert = self._scrubbed(alert)
        self._log(alert)
        if session is None:
            return None
        return self._persist(
            session,
            alert,
            webhook_url=webhook_url,
            rule_id=rule_id,
            # No webhook dispatched yet on the sync path.
            delivery_status=("pending" if webhook_url else "log_only"),
        )

    async def emit_and_deliver(
        self,
        alert: Alert,
        session: "Session",
        webhook_url: str | None = None,
        rule_id: str | None = None,
    ) -> AlertEventORM:
        """Log, persist, and (if URL set) deliver to webhook.

        Updates the persisted ``alert_events`` row with the delivery
        outcome after the HTTP call returns.
        """
        alert = self._scrubbed(alert)
        self._log(alert)
        event = self._persist(
            session,
            alert,
            webhook_url=webhook_url,
            rule_id=rule_id,
            delivery_status=("pending" if webhook_url else "log_only"),
        )

        results: list[bool] = []
        if webhook_url:
            delivery = await self._webhook_service.deliver(
                webhook_url, self.build_payload(alert)
            )
            results.append(delivery.status == DeliveryStatus.DELIVERED)

        # FIX129 A4 — every fired rule also goes to the configured channels
        # (ntfy / email / webhook), not only to the rule's own webhook_url.
        from app.services import alert_channels

        if alert_channels.configured_channels():
            outcomes = await alert_channels.deliver_async(
                alert_channels.Notification(
                    title=alert.message[:120],
                    message=self._notification_text(alert),
                    severity=alert.severity.value,
                    source="alert-rule",
                    tags=(alert.alert_type.value,),
                    details={k: v for k, v in alert.details.items() if k != "sessions"},
                )
            )
            results.extend(outcomes.values())

        if results:
            event.delivery_status = "delivered" if any(results) else "failed"
            session.flush()

        return event

    @staticmethod
    def _scrubbed(alert: Alert) -> Alert:
        """FIX130 E4 — the alert as it may be logged, stored and sent: no
        SQL, credentials, tokens or user content (``alert_scrub``)."""
        return dataclasses.replace(
            alert, message=scrub_text(alert.message), details=scrub_value(alert.details)
        )

    @staticmethod
    def _notification_text(alert: Alert) -> str:
        lines = [alert.message]
        for k in ("window_minutes", "metric_value", "threshold", "attempts", "successes",
                  "errors", "total", "stuck_age_s"):
            if k in alert.details:
                lines.append(f"{k}: {alert.details[k]}")
        # FIX130 E4 — per session: pid, state, application, age. The query
        # head is NOT sent (it stays in the pg_stuck_transaction log line).
        sessions = alert.details.get("sessions") or []
        for s in sessions[:3]:
            lines.append(
                f"pid {s.get('pid')} {s.get('state')} {s.get('application_name') or '-'} "
                f"{s.get('xact_age_s')} s"
            )
        return scrub_text("\n".join(lines))

    @staticmethod
    def build_payload(alert: Alert) -> dict:
        """Payload POSTed to a webhook_url when an alert fires."""
        return {
            "event": "alert_fired",
            "alert_type": alert.alert_type.value,
            "severity": alert.severity.value,
            "message": scrub_text(alert.message),
            "details": scrub_value(alert.details),
            "tenant_id": str(alert.tenant_id) if alert.tenant_id else None,
            "project_id": str(alert.project_id) if alert.project_id else None,
            "fired_at": alert.timestamp.isoformat(),
        }

    # ------------------------------------------------------------------
    # Legacy helpers — sync, log-only, used only by test_alert_service.py
    # ------------------------------------------------------------------

    def budget_warning(
        self, tenant_id: str, project_id: str, usage: float, limit: float
    ) -> None:
        pct = round((usage / limit) * 100, 1) if limit > 0 else 0
        self.emit(Alert(
            alert_type=AlertType.BUDGET_THRESHOLD,
            severity=AlertSeverity.WARNING if pct < 100 else AlertSeverity.CRITICAL,
            tenant_id=tenant_id,
            project_id=project_id,
            message=f"Budget usage at {pct}% (${usage:.4f} / ${limit:.2f})",
            details={"usage_usd": usage, "limit_usd": limit, "percent": pct},
        ))

    def sensitive_action(
        self, tenant_id: str, action: str, actor_key_id: str, details: dict | None = None
    ) -> None:
        self.emit(Alert(
            alert_type=AlertType.SENSITIVE_ACTION,
            severity=AlertSeverity.INFO,
            tenant_id=tenant_id,
            message=f"Sensitive action: {action}",
            details={"action": action, "actor_key_id": actor_key_id, **(details or {})},
        ))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _log(self, alert: Alert) -> None:
        logger.warning(
            "alert_emitted",
            alert_type=alert.alert_type.value,
            severity=alert.severity.value,
            tenant_id=alert.tenant_id,
            project_id=alert.project_id,
            message=alert.message,
            details=alert.details,
        )

    def _persist(
        self,
        session: "Session",
        alert: Alert,
        *,
        webhook_url: str | None,
        rule_id: str | None,
        delivery_status: str,
    ) -> AlertEventORM:
        event = AlertEventORM(
            id=str(uuid.uuid4()),
            rule_id=rule_id,
            alert_type=alert.alert_type.value,
            severity=alert.severity.value,
            message=alert.message,
            details_json=(
                json.dumps(alert.details, ensure_ascii=False)
                if alert.details else None
            ),
            tenant_id=alert.tenant_id or None,
            project_id=alert.project_id or None,
            webhook_url=webhook_url,
            delivery_status=delivery_status,
        )
        session.add(event)
        session.flush()
        return event
