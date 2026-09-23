"""Alert rule evaluator — queries request_logs aggregates and fires
matching ``AlertRuleORM`` rules through ``AlertService``.

Called once per completed request on both the non-streaming and
streaming paths. The evaluator is intentionally cheap: for each
enabled rule whose scope matches the request that just completed it
runs a single aggregate over ``request_logs`` restricted to the rule's
``window_minutes``.

Metrics (aligned 1:1 with ``AlertType``):
    - ``dlp_block_spike``    — count of ``policy_action='block'`` rows
    - ``provider_failure``   — count of ``status='error'`` rows
    - ``fallback_spike``     — count of ``fallback_used=true`` rows (FIX120)
    - ``budget_threshold``   — sum of ``estimated_cost_usd`` rows
    - ``quota_exhausted``    — count of any rows

Scope:
    - ``global``  — no tenant/project filter
    - ``tenant``  — ``request_logs.tenant_id == rule.scope_id``
    - ``project`` — ``request_logs.project_id == rule.scope_id``

Cooldown:
    A rule that fires updates ``last_fired_at`` and will be silently
    skipped by subsequent calls until ``window_minutes`` have passed.
    This keeps a single incident from generating N alerts when N
    matching requests arrive in quick succession.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select

from app.models.alert_rule import AlertRuleORM
from app.models.request_log import RequestLogORM
from app.services.alert_service import Alert, AlertService, AlertSeverity, AlertType

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)


# Metric name → AlertType mapping. Only metrics listed here are
# actively evaluated; rules with unknown metrics are silently skipped
# (they still live in the DB as configuration history).
_METRIC_TO_TYPE: dict[str, AlertType] = {
    "dlp_block_spike": AlertType.DLP_BLOCK_SPIKE,
    "provider_failure": AlertType.PROVIDER_FAILURE,
    # FIX120 — count of rows where a fallback model answered (fallback_used)
    "fallback_spike": AlertType.FALLBACK_SPIKE,
    "budget_threshold": AlertType.BUDGET_THRESHOLD,
    "quota_exhausted": AlertType.QUOTA_EXHAUSTED,
    # FIX129 A4 — the baseline rules. ``no_successful_turn`` = requests were
    # attempted in the window and none succeeded (a quiet night is not an
    # outage); ``error_rate`` = % of non-success, non-blocked rows once at
    # least ERROR_RATE_MIN_SAMPLE requests are in the window;
    # ``stuck_transaction`` = the pg_stat_activity sampler's oldest idle /
    # lock-blocked transaction age in seconds (no request_logs query).
    "no_successful_turn": AlertType.NO_SUCCESSFUL_TURN,
    "error_rate": AlertType.ERROR_RATE,
    "stuck_transaction": AlertType.STUCK_TRANSACTION,
}

ERROR_RATE_MIN_SAMPLE = 10
# A DLP block is a policy outcome, not a failure of the system.
_NON_ERROR_STATUSES = ("success", "blocked")


class AlertEvaluator:
    def __init__(self, alert_service: AlertService | None = None) -> None:
        self.alert_service = alert_service or AlertService()

    async def evaluate_after_request(
        self,
        session: "Session",
        request_log: RequestLogORM,
    ) -> list[str]:
        """Evaluate every enabled rule against the just-completed request.

        Returns a list of rule_ids that fired (useful for testing).
        Caller is expected to wrap this in ``try/except`` — alert
        evaluation must never propagate errors back into the request
        response path.
        """
        rows = (
            session.execute(
                select(AlertRuleORM).where(AlertRuleORM.enabled == True)  # noqa: E712
            )
            .scalars()
            .all()
        )

        fired: list[str] = []
        now = datetime.now(timezone.utc)

        for rule in rows:
            if rule.metric not in _METRIC_TO_TYPE:
                continue
            if not self._scope_matches(rule, request_log):
                continue
            if self._in_cooldown(rule, now):
                continue

            value = self._compute_metric(session, rule, now)
            if value < rule.threshold:
                continue

            alert = self._build_alert(rule, value)
            try:
                await self.alert_service.emit_and_deliver(
                    alert,
                    session=session,
                    webhook_url=rule.webhook_url,
                    rule_id=rule.id,
                )
            except Exception as exc:
                # Webhook delivery failed catastrophically — already
                # logged by WebhookService. Don't propagate.
                logger.warning(
                    "alert_emit_failed", rule_id=rule.id, error=str(exc)
                )
                continue

            rule.last_fired_at = now
            session.flush()
            fired.append(rule.id)

        return fired

    async def evaluate_all(self, session: "Session") -> list[str]:
        """Evaluate every enabled rule against its own scope window.

        Phase-1 B6: the periodic background-task entry point. The old
        ``evaluate_after_request`` ran this analytical work (an enabled-
        rules SELECT + a window aggregate per matching rule) on the
        request hot path, on the event loop, once per completed request.
        Every metric here is a *time-window aggregate* keyed on the
        rule's own scope (``_compute_metric`` already filters by
        ``scope_id``), so it does not need a per-request trigger — a
        periodic sweep is functionally equivalent and keeps the request
        path free. ``request_log`` / ``_scope_matches`` are therefore no
        longer involved.

        Returns the list of rule_ids that fired.

        H15: this runs on a periodic background sweep. The enabled-rules
        SELECT and the per-rule window aggregate (``_compute_metric``,
        which scans ``request_logs`` over the rule window) are synchronous
        SQLAlchemy calls — run them via ``asyncio.to_thread`` so the sweep
        never blocks the event loop. The session is used sequentially
        (never concurrently), so threading it is safe. Webhook delivery
        stays awaited (it's async + bounded, so it yields the loop).
        """
        rows = await asyncio.to_thread(self._fetch_enabled_rules, session)

        fired: list[str] = []
        now = datetime.now(timezone.utc)

        for rule in rows:
            if rule.metric not in _METRIC_TO_TYPE:
                continue
            if self._in_cooldown(rule, now):
                continue

            value = await asyncio.to_thread(
                self._compute_metric, session, rule, now
            )
            if value < rule.threshold:
                continue

            alert = self._build_alert(rule, value)
            try:
                await self.alert_service.emit_and_deliver(
                    alert,
                    session=session,
                    webhook_url=rule.webhook_url,
                    rule_id=rule.id,
                )
            except Exception as exc:
                logger.warning(
                    "alert_emit_failed", rule_id=rule.id, error=str(exc)
                )
                continue

            await asyncio.to_thread(self._mark_fired, session, rule, now)
            fired.append(rule.id)

        return fired

    @staticmethod
    def _fetch_enabled_rules(session: "Session") -> list[AlertRuleORM]:
        return (
            session.execute(
                select(AlertRuleORM).where(AlertRuleORM.enabled == True)  # noqa: E712
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _mark_fired(
        session: "Session", rule: AlertRuleORM, now: datetime
    ) -> None:
        rule.last_fired_at = now
        session.flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_matches(
        rule: AlertRuleORM, request_log: RequestLogORM
    ) -> bool:
        if rule.scope == "global":
            return True
        if rule.scope == "tenant":
            return rule.scope_id == request_log.tenant_id
        if rule.scope == "project":
            return rule.scope_id == request_log.project_id
        return False

    @staticmethod
    def _in_cooldown(rule: AlertRuleORM, now: datetime) -> bool:
        if rule.last_fired_at is None:
            return False
        last = rule.last_fired_at
        if last.tzinfo is None:
            # SQLite may hand back naive datetimes even though the
            # column is declared timezone-aware — assume UTC.
            last = last.replace(tzinfo=timezone.utc)
        return (now - last) < timedelta(minutes=rule.window_minutes)

    @staticmethod
    def _compute_metric(
        session: "Session", rule: AlertRuleORM, now: datetime
    ) -> float:
        window_start = now - timedelta(minutes=rule.window_minutes)
        filters = [RequestLogORM.created_at >= window_start]
        if rule.scope == "tenant" and rule.scope_id:
            filters.append(RequestLogORM.tenant_id == rule.scope_id)
        elif rule.scope == "project" and rule.scope_id:
            filters.append(RequestLogORM.project_id == rule.scope_id)

        if rule.metric == "dlp_block_spike":
            stmt = select(func.count()).select_from(RequestLogORM).where(
                *filters, RequestLogORM.policy_action == "block"
            )
        elif rule.metric == "provider_failure":
            stmt = select(func.count()).select_from(RequestLogORM).where(
                *filters, RequestLogORM.status == "error"
            )
        elif rule.metric == "fallback_spike":
            stmt = select(func.count()).select_from(RequestLogORM).where(
                *filters, RequestLogORM.fallback_used.is_(True)
            )
        elif rule.metric == "budget_threshold":
            stmt = select(func.coalesce(func.sum(RequestLogORM.estimated_cost_usd), 0.0)).where(
                *filters
            )
        elif rule.metric == "quota_exhausted":
            stmt = select(func.count()).select_from(RequestLogORM).where(*filters)
        elif rule.metric == "stuck_transaction":
            from app.services import pg_activity

            return float(pg_activity.stuck_age_seconds())
        elif rule.metric in ("no_successful_turn", "error_rate"):
            total = int(
                session.execute(
                    select(func.count()).select_from(RequestLogORM).where(*filters)
                ).scalar() or 0
            )
            successes = int(
                session.execute(
                    select(func.count()).select_from(RequestLogORM).where(
                        *filters, RequestLogORM.status == "success"
                    )
                ).scalar() or 0
            )
            if rule.metric == "no_successful_turn":
                return float(total) if total >= 1 and successes == 0 else 0.0
            errors = int(
                session.execute(
                    select(func.count()).select_from(RequestLogORM).where(
                        *filters, RequestLogORM.status.notin_(_NON_ERROR_STATUSES)
                    )
                ).scalar() or 0
            )
            if total < ERROR_RATE_MIN_SAMPLE:
                return 0.0
            return round(100.0 * errors / total, 2)
        else:
            return 0.0

        value = session.execute(stmt).scalar() or 0
        return float(value)

    @staticmethod
    def _build_alert(rule: AlertRuleORM, value: float) -> Alert:
        alert_type = _METRIC_TO_TYPE[rule.metric]
        # Escalate severity when the observed value is ≥ 2× the
        # configured threshold — that's the "something is really wrong"
        # zone operators want surfaced as critical.
        if rule.threshold > 0 and value >= rule.threshold * 2:
            severity = AlertSeverity.CRITICAL
        else:
            severity = AlertSeverity.WARNING

        details = {
            "rule_id": rule.id,
            "metric": rule.metric,
            "metric_value": value,
            "threshold": rule.threshold,
            "window_minutes": rule.window_minutes,
        }
        # FIX129 — say what happened in words a phone notification can show.
        if rule.metric == "no_successful_turn":
            message = (
                f"{rule.name}: {int(value)} request(s) in the last "
                f"{rule.window_minutes} min, none succeeded"
            )
        elif rule.metric == "error_rate":
            message = (
                f"{rule.name}: {value:.1f} % of requests failed in the last "
                f"{rule.window_minutes} min (limit {rule.threshold:g} %)"
            )
        elif rule.metric == "stuck_transaction":
            from app.services import pg_activity

            sample = pg_activity.last_sample()
            message = (
                f"{rule.name}: a database transaction has been stuck for "
                f"{value:.0f} s (limit {rule.threshold:g} s)"
            )
            if sample is not None:
                details["stuck_age_s"] = round(sample.stuck_age_s, 1)
                details["sessions"] = sample.worst[:5]
        else:
            message = (
                f"{rule.name}: {rule.metric} threshold {rule.threshold} "
                f"exceeded (observed {value})"
            )

        return Alert(
            alert_type=alert_type,
            severity=severity,
            tenant_id=rule.scope_id if rule.scope == "tenant" else "",
            project_id=rule.scope_id if rule.scope == "project" else "",
            message=message,
            details=details,
            timestamp=datetime.now(timezone.utc),
        )
