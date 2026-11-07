"""FIX129 A4 — the baseline alert rules every deployment gets.

``alert_rules`` was empty on the live system through a 34-minute outage.
At startup the backend inserts these three rules when no enabled or
disabled rule with the same ``metric`` exists yet (so an operator who
edits or disables one is never overridden). Container health, disk and
memory are watched by the ops watcher (``app/ops/watch.py``), which sees
the Docker socket; the rules here are the ones the request log and the
pg_stat_activity sampler can answer.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from app.models.alert_rule import AlertRuleORM

logger = structlog.get_logger(__name__)

DEFAULT_RULES: tuple[dict, ...] = (
    {
        "name": "No successful turn in 5 min (with attempts)",
        "metric": "no_successful_turn",
        "threshold": 1.0,
        "window_minutes": 5,
    },
    {
        "name": "Error rate above 5 % (5 min, ≥10 requests)",
        "metric": "error_rate",
        "threshold": 5.0,
        "window_minutes": 5,
    },
    {
        "name": "Transaction stuck longer than 60 s",
        "metric": "stuck_transaction",
        "threshold": 60.0,
        "window_minutes": 1,
    },
)


def ensure_default_rules(session) -> list[str]:
    """Insert missing default rules (by metric). Returns the names added."""
    existing = set(
        session.execute(select(AlertRuleORM.metric)).scalars().all()
    )
    added: list[str] = []
    for rule in DEFAULT_RULES:
        if rule["metric"] in existing:
            continue
        session.add(
            AlertRuleORM(
                id=str(uuid.uuid4()),
                name=rule["name"],
                metric=rule["metric"],
                threshold=float(rule["threshold"]),
                window_minutes=int(rule["window_minutes"]),
                scope="global",
                enabled=True,
            )
        )
        added.append(rule["name"])
    if added:
        session.flush()
        logger.info("alert_default_rules_added", rules=added)
    return added
