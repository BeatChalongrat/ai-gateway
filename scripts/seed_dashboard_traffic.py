"""Seed synthetic request_logs across the last 48h for dashboard demo.

Run via the backend container so it picks up the same DB settings the app
uses:

    docker exec ai-gateway-backend-1 python /app/scripts/seed_dashboard_traffic.py

Current 24h window gets the "target" shape described in the issue
(80 requests, ~40/30/20/10 provider mix, ~97% success, p50=500ms / p95=2s).
Prior 24h gets a slightly different total (~65) so the delta chips land
on real ▲ / ▼ values rather than on the "no prior data" branch.
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Make sure we can import the app when invoked from anywhere.
sys.path.insert(0, "/app")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings  # noqa: E402
from app.models.request_log import RequestLogORM  # noqa: E402

TENANT_ID = "demo-tenant-000"
PROJECT_ID = "demo-project-000"

PROVIDERS = [
    ("openai", ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"], 0.40),
    ("anthropic", ["claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"], 0.30),
    ("google", ["gemini-1.5-pro", "gemini-1.5-flash"], 0.20),
    ("mistral", ["mistral-large", "mistral-small"], 0.06),
    ("groq", ["llama-3.1-70b-versatile"], 0.04),
]

COST_PER_MODEL = {
    "gpt-4o": 0.015,
    "gpt-4o-mini": 0.0008,
    "gpt-3.5-turbo": 0.0015,
    "claude-3-5-sonnet-20241022": 0.012,
    "claude-3-haiku-20240307": 0.0008,
    "gemini-1.5-pro": 0.008,
    "gemini-1.5-flash": 0.0006,
    "mistral-large": 0.010,
    "mistral-small": 0.002,
    "llama-3.1-70b-versatile": 0.0009,
}

ERROR_STATES = [
    ("blocked", "dlp_blocked_pii"),
    ("provider_error", "provider_429"),
]


def _pick_provider() -> tuple[str, str]:
    r = random.random()
    cum = 0.0
    for provider, models, weight in PROVIDERS:
        cum += weight
        if r <= cum:
            return provider, random.choice(models)
    provider, models, _ = PROVIDERS[0]
    return provider, random.choice(models)


def _latency_ms() -> int:
    """Log-normal-ish distribution: p50 ~500ms, p95 ~2000ms, occasional tails."""
    roll = random.random()
    if roll < 0.50:
        return random.randint(150, 700)
    if roll < 0.85:
        return random.randint(700, 1500)
    if roll < 0.97:
        return random.randint(1500, 2500)
    return random.randint(2500, 6000)


def _generate_row(ts: datetime, success_rate: float) -> dict:
    provider, model = _pick_provider()
    prompt_tok = random.randint(80, 2000)
    completion_tok = random.randint(40, 800)
    total_tok = prompt_tok + completion_tok

    if random.random() < success_rate:
        status, err = "success", None
    else:
        status, err = random.choice(ERROR_STATES)

    cost_per_1k = COST_PER_MODEL.get(model, 0.001)
    cost = (total_tok / 1000.0) * cost_per_1k

    return {
        "request_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "provider": provider,
        "model": model,
        "status": status,
        "policy_action": "allow",
        "latency_ms": _latency_ms(),
        "prompt_tokens": prompt_tok,
        "completion_tokens": completion_tok if status == "success" else 0,
        "total_tokens": total_tok if status == "success" else prompt_tok,
        "estimated_cost_usd": round(cost, 6) if status == "success" else 0.0,
        "error_code": err,
        "created_at": ts,
    }


def _spread_timestamps(start: datetime, end: datetime, count: int) -> list[datetime]:
    """Random timestamps uniformly spread across [start, end) — not bunched."""
    total_seconds = int((end - start).total_seconds())
    offsets = sorted(random.sample(range(total_seconds), min(count, total_seconds)))
    return [start + timedelta(seconds=o) for o in offsets]


def seed() -> None:
    random.seed(42)  # Deterministic-ish demo data.

    now = datetime.now(timezone.utc)
    # Leave a 30-min gap at the head so timestamps don't land in the future
    # if the clock drifts between container and host.
    current_end = now - timedelta(minutes=5)
    current_start = current_end - timedelta(hours=24)
    prior_end = current_start
    prior_start = prior_end - timedelta(hours=24)

    current_count = 80
    prior_count = 65

    rows: list[dict] = []
    for ts in _spread_timestamps(current_start, current_end, current_count):
        rows.append(_generate_row(ts, success_rate=0.97))
    for ts in _spread_timestamps(prior_start, prior_end, prior_count):
        rows.append(_generate_row(ts, success_rate=0.94))

    # Use the same DSN the running app uses so we hit the same DB / schema.
    engine = create_engine(str(settings.database_url))
    with Session(engine) as session:
        session.bulk_insert_mappings(RequestLogORM, rows)
        session.commit()

    print(
        f"Seeded {len(rows)} rows "
        f"({current_count} in current 24h, {prior_count} in prior 24h). "
        f"Tenant={TENANT_ID} Project={PROJECT_ID}"
    )


if __name__ == "__main__":
    seed()
