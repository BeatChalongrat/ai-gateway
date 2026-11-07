"""Replicate webhook receiver (Sprint 2B).

``POST /webhooks/replicate?secret=...`` — Replicate calls this when a
prediction starts/completes (configured by the adapter when a webhook
secret is set). It reduces polling load and speeds up the "done"
notification; the job-loop poll stays as the fallback.

Safety:
- Timing-safe secret compare (401 on mismatch).
- IDEMPOTENT: a job already ``done``/``failed`` (poll loop or a duplicate
  webhook beat us) is a 200 no-op — no duplicate file, no double spend
  commit/refund. An UNKNOWN prediction id is also a 200 no-op (never 404 —
  that would trigger a Replicate retry storm).
- On success → persist the artifact + ``commit_daily_spend`` (L2). On
  failure → ``release_session_budget`` (L1 refund). Heavy work is small
  (one short download) so we do it inline and respond 200.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_db
from app.models.generation_job import GenerationJobORM
from app.observability.metrics import GEN_COST_CREDITS, GEN_FAILURE_TOTAL
from app.services.media_gen.registry import get_adapter
from app.services.media_gen.spend_guard import (
    commit_daily_spend,
    release_session_budget,
)

log = logging.getLogger(__name__)
router = APIRouter()

_OK = JSONResponse({"ok": True})


def _output_dir() -> str:
    from app.services.media_gen.job_loop import _output_dir as d

    return d()


@router.post("/replicate")
async def replicate_webhook(
    request: Request,
    secret: str = "",
    session: AsyncSession = Depends(get_async_db),
) -> JSONResponse:
    configured = (settings.replicate_webhook_secret or "").strip()
    if not configured or not secret or not hmac.compare_digest(secret, configured):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is a no-op, not a 500
        return _OK

    pred_id = str(payload.get("id") or "")
    rep_status = str(payload.get("status") or "")
    if not pred_id:
        return _OK

    job = (
        await session.execute(
            select(GenerationJobORM)
            .where(GenerationJobORM.external_job_id == pred_id)
            .with_for_update()  # serialise vs the poll loop on Postgres
        )
    ).scalars().first()
    # Unknown id OR already terminal → idempotent 200 no-op.
    if job is None or job.status in ("done", "failed", "cancelled"):
        return _OK

    meta = job.input_metadata or {}
    estimate_usd = Decimal(str(meta.get("estimate_usd", "0") or "0"))

    if rep_status == "succeeded":
        output = payload.get("output")
        if isinstance(output, list):
            url = output[0] if output else None
        else:
            url = output if isinstance(output, str) else None
        if not url:
            return _OK
        try:
            adapter = get_adapter(job.provider, job.job_type)
            data = await adapter.fetch_result_bytes(url)
        except Exception:  # noqa: BLE001 — leave the job in-flight for the poll fallback
            log.exception("replicate webhook: fetch failed for job %s", job.id)
            return _OK
        # Phase 10 (A1): derive extension + MIME from the job type / result
        # URL — a ``replicate_music`` artifact must not be persisted as
        # ``.mp4`` + ``video/mp4``. Same helper as the job-loop path.
        from app.services.media_gen.job_loop import resolve_output_ext_and_type

        ext, content_type = resolve_output_ext_and_type(job.job_type, None, url)
        out_path = os.path.join(_output_dir(), f"{job.id}{ext}")
        with open(out_path, "wb") as fh:
            fh.write(data)
        actual = int(job.estimated_cost_credits)
        job.status = "done"
        job.progress_pct = 100
        job.output_metadata = {
            "output_path": out_path,
            "content_type": content_type,
            "size": len(data),
        }
        job.actual_cost_credits = actual
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await commit_daily_spend(job.tenant_id, estimate_usd)
        try:
            GEN_COST_CREDITS.labels(job.job_type, job.provider).observe(actual)
        except Exception:  # noqa: BLE001
            pass
        log.info("replicate webhook: job %s done (%dB)", job.id, len(data))
    elif rep_status in ("failed", "canceled"):
        job.status = "failed"
        job.error_code = (rep_status or "failed")[:64]
        job.error_message = (str(payload.get("error") or "")[:500] or "provider failure")
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await release_session_budget(estimate_usd)
        try:
            GEN_FAILURE_TOTAL.labels(job.job_type, job.provider, job.error_code).inc()
        except Exception:  # noqa: BLE001
            pass
        log.info("replicate webhook: job %s failed (%s)", job.id, job.error_code)
    # Other statuses (e.g. "starting") → 200 no-op.
    return _OK
