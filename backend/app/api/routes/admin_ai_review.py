"""Admin routes for the AI Sensitive Data Reviewer feature.

Three surfaces:

- ``/ai-review/settings`` (GET/PATCH) — singleton feature flag +
  scope + dedicated provider config + realtime toggle.
- ``/ai-review/scan`` (POST) — manual scan, uses the dedicated
  provider/key and records a ``ai_review_scan_runs`` row with token
  + cost aggregates.
- ``/ai-review/scan-runs`` (GET) + ``/scan-runs/summary`` (GET) — scan
  execution history used by the admin dashboard and Usage tab.
- ``/ai-review/findings`` (GET/PATCH) — findings produced by any run.

The realtime hook lives at the bottom of this module as
``maybe_ai_review_background`` — public_chat.py and orchestrator.py
import it and fire it off with ``asyncio.create_task`` after each
request completes.

Auth: mounted on the admin router, so every endpoint inherits the
``require_admin`` dependency.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.db import SessionLocal
from app.core.exceptions import NotFoundError, ValidationError
from app.models.ai_review import (
    AiReviewFindingORM,
    AiReviewScanRunORM,
    AiReviewSettingsORM,
)
from app.models.api_key import ApiKeyORM
from app.models.request_log import RequestLogORM
from app.schemas.admin import (
    AiReviewScanRun as AiReviewScanRunSchema,
    AiReviewScanRunSummary,
    AiReviewSettings as AiReviewSettingsSchema,
    AiReviewSettingsPatch,
)
from app.services.ai_review_provider import (
    PROVIDER_PRESETS,
    ReviewApiError,
    build_ai_review_prompt,
    call_review_api,
    encode_key,
    estimate_cost,
    parse_findings_json,
    resolve_provider_meta,
    review_egress_allowed,
)
from app.services.dlp.detectors.registry import get_default_registry

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def _get_or_create_settings(session: Session) -> AiReviewSettingsORM:
    row = session.get(AiReviewSettingsORM, 1)
    if row is None:
        row = AiReviewSettingsORM(
            id=1,
            enabled=False,
            scan_limit=50,
            realtime_enabled=False,
            realtime_sample_rate=10,
        )
        session.add(row)
        session.flush()
    return row


def _decode_key_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except Exception:
        return None
    return decoded if isinstance(decoded, list) else None


def _settings_view(row: AiReviewSettingsORM) -> AiReviewSettingsSchema:
    return AiReviewSettingsSchema(
        enabled=row.enabled,
        monitored_key_ids=_decode_key_ids(row.monitored_key_ids),
        scan_limit=row.scan_limit,
        last_scan_at=row.last_scan_at,
        review_provider=row.review_provider,
        review_model=row.review_model,
        review_api_key_set=row.review_api_key_enc is not None,
        review_endpoint=row.review_endpoint,
        realtime_enabled=row.realtime_enabled,
        realtime_sample_rate=row.realtime_sample_rate,
    )


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------


@router.get("/ai-review/settings", response_model=AiReviewSettingsSchema)
def get_ai_review_settings(
    session: Session = Depends(get_db_session),
) -> AiReviewSettingsSchema:
    return _settings_view(_get_or_create_settings(session))


_VALID_PROVIDERS = set(PROVIDER_PRESETS.keys())


@router.patch("/ai-review/settings", response_model=AiReviewSettingsSchema)
def patch_ai_review_settings(
    body: AiReviewSettingsPatch,
    session: Session = Depends(get_db_session),
) -> AiReviewSettingsSchema:
    row = _get_or_create_settings(session)
    fields = body.model_fields_set

    if "enabled" in fields and body.enabled is not None:
        row.enabled = body.enabled

    if "monitored_key_ids" in fields:
        row.monitored_key_ids = (
            None if body.monitored_key_ids is None
            else json.dumps(body.monitored_key_ids, ensure_ascii=False)
        )

    if "scan_limit" in fields and body.scan_limit is not None:
        if not (1 <= body.scan_limit <= 200):
            raise ValidationError(
                "scan_limit must be between 1 and 200.",
                details={"scan_limit": body.scan_limit},
            )
        row.scan_limit = body.scan_limit

    if "review_provider" in fields:
        if body.review_provider is not None and body.review_provider not in _VALID_PROVIDERS:
            raise ValidationError(
                f"review_provider must be one of {sorted(_VALID_PROVIDERS)}.",
                details={"received": body.review_provider},
            )
        row.review_provider = body.review_provider

    if "review_model" in fields:
        row.review_model = body.review_model

    if "review_api_key" in fields:
        # Explicit null clears the stored key, falling back to env.
        row.review_api_key_enc = (
            None if body.review_api_key is None else encode_key(body.review_api_key)
        )

    if "review_endpoint" in fields:
        row.review_endpoint = body.review_endpoint

    if "realtime_enabled" in fields and body.realtime_enabled is not None:
        row.realtime_enabled = body.realtime_enabled

    if "realtime_sample_rate" in fields and body.realtime_sample_rate is not None:
        if not (1 <= body.realtime_sample_rate <= 100):
            raise ValidationError(
                "realtime_sample_rate must be between 1 and 100.",
                details={"realtime_sample_rate": body.realtime_sample_rate},
            )
        row.realtime_sample_rate = body.realtime_sample_rate

    session.flush()
    invalidate_settings_cache()
    return _settings_view(row)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _collect_targets(
    session: Session,
    *,
    monitored_key_ids: list[str] | None,
    limit: int,
) -> list[RequestLogORM]:
    stmt = (
        select(RequestLogORM)
        .where(RequestLogORM.output_raw.is_not(None))
        .where(RequestLogORM.output_raw != "")
        .order_by(RequestLogORM.created_at.desc())
        .limit(limit)
    )
    if monitored_key_ids:
        project_rows = session.execute(
            select(ApiKeyORM.project_id).where(
                ApiKeyORM.key_id.in_(monitored_key_ids)
            )
        ).all()
        project_ids = {r[0] for r in project_rows}
        if not project_ids:
            return []
        stmt = stmt.where(RequestLogORM.project_id.in_(project_ids))
    return list(session.execute(stmt).scalars().all())


def _persist_findings(
    session: Session,
    *,
    findings: list[dict],
    scan_run_id: str,
    targets: list[RequestLogORM] | None = None,
    fixed_request_id: str | None = None,
) -> tuple[int, int]:
    """Write parsed findings to the DB.

    Returns ``(new_patterns_count, already_covered_count)``.

    Called in two modes:
    - Manual scan: ``targets`` is the list of seeded request_logs; the
      model's ``request_index`` is used to resolve request_id.
    - Realtime: a single request text — caller passes ``fixed_request_id``
      and all findings are attributed to it.
    """
    new_patterns = 0
    already_covered = 0

    for f in findings:
        if not isinstance(f, dict):
            continue

        if fixed_request_id is not None:
            request_id = fixed_request_id
        else:
            idx = f.get("request_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(targets or []):
                continue
            request_id = targets[idx].request_id

        entity_type = str(f.get("entity_type") or "unknown")[:100]
        description = str(f.get("description") or "")
        sample_text = str(f.get("sample_text") or "")[:100]
        risk_level = str(f.get("risk_level") or "LOW").upper()
        if risk_level not in ("HIGH", "MEDIUM", "LOW"):
            risk_level = "LOW"
        is_in_coverage = bool(f.get("is_in_coverage", False))
        suggested = f.get("suggested_detector")
        suggested_detector = (
            str(suggested)[:100] if suggested is not None else None
        )

        if is_in_coverage:
            already_covered += 1
        else:
            new_patterns += 1

        session.add(
            AiReviewFindingORM(
                id=str(uuid.uuid4()),
                request_id=request_id,
                scan_run_id=scan_run_id,
                entity_type=entity_type,
                description=description,
                sample_text=sample_text,
                risk_level=risk_level,
                is_in_coverage=is_in_coverage,
                suggested_detector=suggested_detector,
                status="new",
            )
        )

    return new_patterns, already_covered


class AiReviewScanResponse(BaseModel):
    scan_run_id: str | None
    findings_count: int
    new_patterns_count: int
    already_covered_count: int
    tokens_used: int = 0
    estimated_cost_usd: float = 0.0


@router.post("/ai-review/scan", response_model=AiReviewScanResponse)
async def run_ai_review_scan(
    session: Session = Depends(get_db_session),
) -> AiReviewScanResponse:
    row = _get_or_create_settings(session)
    if not row.enabled:
        raise ValidationError(
            "AI review is disabled. Enable it in settings first.",
            details={"enabled": False},
        )

    monitored = _decode_key_ids(row.monitored_key_ids)
    targets = _collect_targets(
        session, monitored_key_ids=monitored, limit=row.scan_limit
    )
    if not targets:
        row.last_scan_at = datetime.now(timezone.utc)
        session.flush()
        return AiReviewScanResponse(
            scan_run_id=None,
            findings_count=0,
            new_patterns_count=0,
            already_covered_count=0,
        )

    # Resolve provider meta up front so we can populate the scan_run
    # row even if the call fails mid-flight.
    meta = resolve_provider_meta(row)

    scan_run = AiReviewScanRunORM(
        scan_run_id=str(uuid.uuid4()),
        trigger="manual",
        provider=meta["provider"],
        model=meta["model"],
        requests_scanned=len(targets),
        findings_count=0,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        estimated_cost_usd=Decimal("0"),
        status="completed",
        error_message=None,
    )
    session.add(scan_run)
    session.flush()

    texts = [t.output_raw or "" for t in targets]
    detector_names = [d.name for d in get_default_registry().all()]
    system, user = build_ai_review_prompt(texts, detector_names)

    try:
        norm = await call_review_api(row, system_text=system, user_text=user)
        findings_raw = parse_findings_json(norm["content"])
    except ValueError as exc:
        # No API key — surface as a 422 so the UI can prompt the admin.
        scan_run.status = "failed"
        scan_run.error_message = str(exc)
        session.flush()
        raise ValidationError(str(exc), details={"step": "key_resolution"}) from exc
    except ReviewApiError as exc:
        scan_run.status = "failed"
        scan_run.error_message = str(exc)
        session.flush()
        raise ValidationError(
            f"AI review scan failed: {exc}",
            details={"step": "upstream_call"},
        ) from exc

    new_patterns, already_covered = _persist_findings(
        session,
        findings=findings_raw,
        scan_run_id=scan_run.scan_run_id,
        targets=targets,
    )

    prompt_tokens = int(norm.get("prompt_tokens") or 0)
    completion_tokens = int(norm.get("completion_tokens") or 0)
    total_tokens = prompt_tokens + completion_tokens
    cost_value = estimate_cost(meta["provider"], prompt_tokens, completion_tokens)

    scan_run.prompt_tokens = prompt_tokens
    scan_run.completion_tokens = completion_tokens
    scan_run.total_tokens = total_tokens
    scan_run.findings_count = new_patterns + already_covered
    scan_run.estimated_cost_usd = Decimal(str(cost_value))

    row.last_scan_at = datetime.now(timezone.utc)
    session.flush()

    return AiReviewScanResponse(
        scan_run_id=scan_run.scan_run_id,
        findings_count=new_patterns + already_covered,
        new_patterns_count=new_patterns,
        already_covered_count=already_covered,
        tokens_used=total_tokens,
        estimated_cost_usd=float(cost_value),
    )


# ---------------------------------------------------------------------------
# Findings CRUD
# ---------------------------------------------------------------------------


class AiReviewFindingPatch(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)


_ALLOWED_STATUSES = ("new", "reviewed", "added_to_dlp", "dismissed")


@router.get("/ai-review/findings")
def list_ai_review_findings(
    status: str | None = None,
    scan_run_id: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise ValidationError("limit must be between 1 and 500.")

    stmt = select(AiReviewFindingORM).order_by(
        AiReviewFindingORM.created_at.desc()
    )
    if status:
        stmt = stmt.where(AiReviewFindingORM.status == status)
    if scan_run_id:
        stmt = stmt.where(AiReviewFindingORM.scan_run_id == scan_run_id)
    stmt = stmt.limit(limit)
    rows = session.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "request_id": r.request_id,
                "scan_run_id": r.scan_run_id,
                "entity_type": r.entity_type,
                "description": r.description,
                "sample_text": r.sample_text,
                "risk_level": r.risk_level,
                "is_in_coverage": r.is_in_coverage,
                "suggested_detector": r.suggested_detector,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.patch("/ai-review/findings/{finding_id}")
def patch_ai_review_finding(
    finding_id: str,
    body: AiReviewFindingPatch,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if body.status not in _ALLOWED_STATUSES:
        raise ValidationError(
            f"status must be one of {_ALLOWED_STATUSES}.",
            details={"received": body.status},
        )
    row = session.get(AiReviewFindingORM, finding_id)
    if row is None:
        raise NotFoundError(f"Finding '{finding_id}' not found.")
    row.status = body.status
    session.flush()
    return {"id": row.id, "status": row.status}


# ---------------------------------------------------------------------------
# Summary (findings)
# ---------------------------------------------------------------------------


class AiReviewSummaryResponse(BaseModel):
    total_findings: int
    new_findings: int
    new_patterns: int
    already_covered: int
    last_scan_at: datetime | None
    enabled: bool
    monitored_key_ids: list[str] | None


@router.get("/ai-review/summary", response_model=AiReviewSummaryResponse)
def get_ai_review_summary(
    session: Session = Depends(get_db_session),
) -> AiReviewSummaryResponse:
    row = _get_or_create_settings(session)

    total = session.execute(
        select(func.count()).select_from(AiReviewFindingORM)
    ).scalar_one()
    new_count = session.execute(
        select(func.count())
        .select_from(AiReviewFindingORM)
        .where(AiReviewFindingORM.status == "new")
    ).scalar_one()
    new_patterns = session.execute(
        select(func.count())
        .select_from(AiReviewFindingORM)
        .where(AiReviewFindingORM.is_in_coverage.is_(False))
        .where(AiReviewFindingORM.status == "new")
    ).scalar_one()
    already_covered = session.execute(
        select(func.count())
        .select_from(AiReviewFindingORM)
        .where(AiReviewFindingORM.is_in_coverage.is_(True))
    ).scalar_one()

    return AiReviewSummaryResponse(
        total_findings=int(total or 0),
        new_findings=int(new_count or 0),
        new_patterns=int(new_patterns or 0),
        already_covered=int(already_covered or 0),
        last_scan_at=row.last_scan_at,
        enabled=row.enabled,
        monitored_key_ids=_decode_key_ids(row.monitored_key_ids),
    )


# ---------------------------------------------------------------------------
# Scan runs history
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------


class TestConnectionResponse(BaseModel):
    success: bool
    latency_ms: int = 0
    model_used: str = ""
    error: str | None = None


@router.post("/ai-review/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    session: Session = Depends(get_db_session),
) -> TestConnectionResponse:
    """Lightweight connectivity check — sends a 1-token prompt and
    measures round-trip latency. Does NOT create a ``scan_run`` row
    so it costs near-zero tokens and doesn't clutter the history."""
    row = _get_or_create_settings(session)
    meta = resolve_provider_meta(row)

    t0 = time.monotonic()
    try:
        norm = await call_review_api(
            row,
            system_text="Reply with the single word: ok",
            user_text="ping",
        )
    except ValueError as exc:
        return TestConnectionResponse(
            success=False, error=str(exc), model_used=meta["model"]
        )
    except ReviewApiError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return TestConnectionResponse(
            success=False, latency_ms=ms, error=str(exc), model_used=meta["model"]
        )

    ms = int((time.monotonic() - t0) * 1000)
    return TestConnectionResponse(
        success=True, latency_ms=ms, model_used=meta["model"]
    )


def _scan_run_to_schema(r: AiReviewScanRunORM) -> AiReviewScanRunSchema:
    return AiReviewScanRunSchema(
        scan_run_id=r.scan_run_id,
        trigger=r.trigger,
        provider=r.provider,
        model=r.model,
        requests_scanned=r.requests_scanned,
        findings_count=r.findings_count,
        prompt_tokens=r.prompt_tokens,
        completion_tokens=r.completion_tokens,
        total_tokens=r.total_tokens,
        estimated_cost_usd=float(r.estimated_cost_usd or 0),
        status=r.status,
        error_message=r.error_message,
        created_at=r.created_at,
    )


@router.get("/ai-review/scan-runs")
def list_scan_runs(
    trigger: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise ValidationError("limit must be between 1 and 500.")

    stmt = select(AiReviewScanRunORM).order_by(
        AiReviewScanRunORM.created_at.desc()
    )
    if trigger:
        stmt = stmt.where(AiReviewScanRunORM.trigger == trigger)
    stmt = stmt.limit(limit)
    rows = session.execute(stmt).scalars().all()

    total_cost_row = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(AiReviewScanRunORM.total_tokens), 0),
            func.coalesce(func.sum(AiReviewScanRunORM.estimated_cost_usd), 0),
        )
    ).one()

    return {
        "items": [_scan_run_to_schema(r).model_dump(mode="json") for r in rows],
        "total": len(rows),
        "aggregate": {
            "total_runs": int(total_cost_row[0] or 0),
            "total_tokens": int(total_cost_row[1] or 0),
            "total_cost_usd": float(total_cost_row[2] or 0),
        },
    }


@router.get("/ai-review/scan-runs/summary", response_model=AiReviewScanRunSummary)
def scan_runs_summary(
    session: Session = Depends(get_db_session),
) -> AiReviewScanRunSummary:
    row = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(AiReviewScanRunORM.estimated_cost_usd), 0),
            func.coalesce(func.sum(AiReviewScanRunORM.total_tokens), 0),
            func.coalesce(
                func.sum(
                    sa_case_when_manual()
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    sa_case_when_realtime()
                ),
                0,
            ),
            func.max(AiReviewScanRunORM.created_at),
        )
    ).one()

    total_runs = int(row[0] or 0)
    total_cost = float(row[1] or 0)
    total_tokens = int(row[2] or 0)
    manual_runs = int(row[3] or 0)
    realtime_runs = int(row[4] or 0)
    last_run_at = row[5]
    avg_cost = (total_cost / total_runs) if total_runs > 0 else 0.0

    return AiReviewScanRunSummary(
        total_runs=total_runs,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        manual_runs=manual_runs,
        realtime_runs=realtime_runs,
        avg_cost_per_run=round(avg_cost, 8),
        last_run_at=last_run_at,
    )


def sa_case_when_manual():
    from sqlalchemy import case

    return case((AiReviewScanRunORM.trigger == "manual", 1), else_=0)


def sa_case_when_realtime():
    from sqlalchemy import case

    return case((AiReviewScanRunORM.trigger == "realtime", 1), else_=0)


# ---------------------------------------------------------------------------
# Realtime hook
# ---------------------------------------------------------------------------


# Settings cache: avoid hitting the DB for every single request the
# gateway serves. Refreshed lazily on expiry. The cache is read-only
# — settings writes always go through a fresh DB read on the next
# request after expiry.
_SETTINGS_CACHE_TTL_SECONDS = 60.0
_settings_cache: dict[str, Any] = {"expires_at": 0.0, "row": None}


def _load_cached_settings() -> dict[str, Any] | None:
    """Return a lightweight snapshot of AI review settings.

    Runs in its own SessionLocal session (the realtime hook is
    invoked outside the request's dep-injected session). Returns
    ``None`` if the DB read fails — callers treat that as "feature
    disabled" to stay out of the request hot path on any error.
    """
    now = time.monotonic()
    if (
        _settings_cache["row"] is not None
        and now < _settings_cache["expires_at"]
    ):
        return _settings_cache["row"]

    try:
        session = SessionLocal()
        try:
            orm = session.get(AiReviewSettingsORM, 1)
            if orm is None:
                snapshot = None
            else:
                snapshot = {
                    "enabled": orm.enabled,
                    "realtime_enabled": orm.realtime_enabled,
                    "realtime_sample_rate": orm.realtime_sample_rate,
                    "monitored_key_ids": _decode_key_ids(orm.monitored_key_ids),
                    "review_provider": orm.review_provider,
                    "review_model": orm.review_model,
                    "review_api_key_enc": orm.review_api_key_enc,
                    "review_endpoint": orm.review_endpoint,
                }
        finally:
            session.close()
    except Exception as exc:
        logger.warning("ai_review_settings_load_failed", error=str(exc))
        return None

    _settings_cache["row"] = snapshot
    _settings_cache["expires_at"] = now + _SETTINGS_CACHE_TTL_SECONDS
    return snapshot


def invalidate_settings_cache() -> None:
    """Force the realtime hook to re-read settings on the next call."""
    _settings_cache["row"] = None
    _settings_cache["expires_at"] = 0.0


# Categories that must NEVER egress to an external reviewer in raw form.
# Mirrors the orchestrator's ``_NEVER_RESTORE_CATEGORIES``.
_REVIEW_FAIL_CLOSED_CATEGORIES = frozenset({"secret", "credential"})


def _assert_no_unmasked_secrets(text: str, request_id: str) -> bool:
    """Return True if ``text`` is safe to send to the external reviewer.

    Defensive egress boundary: re-scans the (expected-to-be-sanitized)
    text with the in-process DLP registry and refuses (returns False) if
    any secret/credential category is still present unmasked. This is a
    cheap, opt-in, background-path check — not a hot-path scan — and
    enforces the Phase 1 rule that a custom reviewer provider can never
    receive raw PII. Logs only counts/categories, never matched text.
    """
    try:
        registry = get_default_registry(ner_enabled=False)
        matches = registry.scan_text(text)
        leaked = sorted(
            {
                m.category
                for m in matches
                if m.category in _REVIEW_FAIL_CLOSED_CATEGORIES
            }
        )
        if leaked:
            logger.error(
                "ai_review_egress_blocked_unmasked_secret",
                request_id=request_id,
                leaked_categories=leaked,
                finding_count=len(matches),
            )
            return False
        return True
    except Exception as exc:
        # If the guard itself fails, fail closed — skip the review.
        logger.warning(
            "ai_review_egress_guard_failed",
            request_id=request_id,
            error=str(exc)[:200],
        )
        return False


async def _maybe_ai_review(
    request_id: str, sanitized_output: str | None, *, api_key_id: str | None = None
) -> None:
    """Fire-and-forget realtime review of a single request's output.

    Scans ``sanitized_output`` — the POST-DLP, PRE-RESTORE masked
    provider output. PII the outbound pipeline already masked is a
    ``<rN>`` token here (so it never reaches the external reviewer);
    PII the pipeline MISSED is still present, which is exactly what the
    reviewer is meant to surface as a coverage gap.

    A defensive egress guard (``_assert_no_unmasked_secrets``) re-scans
    the payload for secret/credential categories and SKIPS the review
    (fail closed) if any unmasked secret slipped through — so a
    custom/third-party reviewer endpoint can never receive a raw secret.

    Runs after the user-facing response has already been sent, so
    every exception is caught and logged — the realtime hook must
    never affect the request.
    """
    try:
        if not sanitized_output or not sanitized_output.strip():
            return

        # Egress guard — never send unmasked secrets/credentials to an
        # external reviewer (incl. operator-custom endpoints). Fail
        # closed: if the guard trips, skip the review entirely. Logs
        # carry only category counts, never matched text.
        if not _assert_no_unmasked_secrets(sanitized_output, request_id):
            return

        snapshot = _load_cached_settings()
        if snapshot is None:
            return
        if not snapshot["enabled"] or not snapshot["realtime_enabled"]:
            return

        rate = max(0, min(100, int(snapshot["realtime_sample_rate"] or 0)))
        if rate <= 0:
            return
        if random.randint(1, 100) > rate:
            return

        monitored = snapshot["monitored_key_ids"]
        if monitored and api_key_id is not None and api_key_id not in monitored:
            return

        # Rebuild a lightweight ORM-shaped object for the provider
        # helpers, which expect AiReviewSettingsORM. We use a plain
        # object with the same attribute names — saves a round-trip
        # through the DB session in the hot path.
        class _SettingsProxy:
            pass

        proxy = _SettingsProxy()
        proxy.review_provider = snapshot["review_provider"]
        proxy.review_model = snapshot["review_model"]
        proxy.review_api_key_enc = snapshot["review_api_key_enc"]
        proxy.review_endpoint = snapshot["review_endpoint"]

        meta = resolve_provider_meta(proxy)  # type: ignore[arg-type]

        # B1 egress trust boundary — the payload may carry missed
        # non-secret PII, so an external non-allowlisted reviewer host is
        # refused (fail closed: skip the review). Logs only the host
        # classification + reason, never the payload text.
        _allowed, _host_class, _ = review_egress_allowed(
            meta.get("base_url"), meta.get("provider")
        )
        if not _allowed:
            logger.error(
                "ai_review_egress_blocked_untrusted_host",
                request_id=request_id,
                reviewer_provider=meta.get("provider"),
                host_classification="external_blocked",
                skipped_reason="endpoint_not_internal_or_allowlisted",
            )
            return

        detector_names = [d.name for d in get_default_registry().all()]
        system, user = build_ai_review_prompt([sanitized_output], detector_names)

        try:
            norm = await call_review_api(
                proxy, system_text=system, user_text=user  # type: ignore[arg-type]
            )
            findings = parse_findings_json(norm["content"])
        except (ValueError, ReviewApiError) as exc:
            # Persist a failed scan_run row so the operator sees that
            # realtime review attempted something even when it fails.
            _persist_failed_scan_run(
                trigger="realtime",
                provider=meta["provider"],
                model=meta["model"],
                error_message=str(exc),
            )
            return

        prompt_tokens = int(norm.get("prompt_tokens") or 0)
        completion_tokens = int(norm.get("completion_tokens") or 0)
        total_tokens = prompt_tokens + completion_tokens
        cost_value = estimate_cost(meta["provider"], prompt_tokens, completion_tokens)

        session = SessionLocal()
        try:
            scan_run = AiReviewScanRunORM(
                scan_run_id=str(uuid.uuid4()),
                trigger="realtime",
                provider=meta["provider"],
                model=meta["model"],
                requests_scanned=1,
                findings_count=0,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=Decimal(str(cost_value)),
                status="completed",
            )
            session.add(scan_run)
            session.flush()

            new_patterns, already_covered = _persist_findings(
                session,
                findings=findings,
                scan_run_id=scan_run.scan_run_id,
                fixed_request_id=request_id,
            )
            scan_run.findings_count = new_patterns + already_covered
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("ai_review_realtime_persist_failed", error=str(exc))
        finally:
            session.close()
    except Exception as exc:
        # Catch-all so the realtime hook never impacts the user path.
        logger.warning("ai_review_realtime_failed", error=str(exc))


def _persist_failed_scan_run(
    *, trigger: str, provider: str, model: str, error_message: str
) -> None:
    session = SessionLocal()
    try:
        session.add(
            AiReviewScanRunORM(
                scan_run_id=str(uuid.uuid4()),
                trigger=trigger,
                provider=provider,
                model=model,
                status="failed",
                error_message=error_message[:500],
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


# Strong references to in-flight realtime tasks so the GC doesn't
# collect them before completion (Python 3.12+ warns about this).
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


def maybe_ai_review_background(
    request_id: str, sanitized_output: str | None, *, api_key_id: str | None = None
) -> None:
    """Schedule ``_maybe_ai_review`` on the running event loop.

    Callers MUST pass ``sanitized_output`` — the POST-DLP, PRE-RESTORE
    masked provider output (``ai_to_service_masked``). Passing the raw
    provider response or the restored ``output_final`` is a contract
    violation; the egress guard in ``_maybe_ai_review`` re-checks for
    unmasked secrets and fails closed regardless. It never raises — a
    missing loop or a closed loop is silently ignored.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        task = loop.create_task(
            _maybe_ai_review(request_id, sanitized_output, api_key_id=api_key_id)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception as exc:
        logger.warning("ai_review_schedule_failed", error=str(exc))
