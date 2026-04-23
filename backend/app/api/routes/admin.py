import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    ResolvedApiKey,
    check_tenant_access,
    get_db_session,
    require_admin_session,
)
from app.core.exceptions import NotFoundError
from app.domain.rbac import Role, has_role_level
from app.models.alert_rule import AlertEventORM, AlertRuleORM
from app.models.api_key import ApiKeyORM
from app.models.keyword_rule import KeywordRuleORM
from app.models.project import ProjectORM
from app.models.provider_key import ProviderKeyORM
from app.models.request_log import RequestLogORM
from app.models.tenant import TenantORM
from app.schemas.admin import (
    ALERT_METRICS,
    ALERT_SCOPES,
    KEYWORD_RULE_SCOPES,
    AdminListResponse,
    AlertEventResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    KeywordRuleCreate,
    KeywordRuleResponse,
    KeywordRuleUpdate,
    RoutingStatsResponse,
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    ModelDetail,
    PolicyCreate,
    PolicyProfileSummary,
    PolicyResponse,
    PolicyUpdate,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    ProviderSummary,
    RequestLogDetail,
    RequestLogSummary,
    TenantCreate,
    TenantResponse,
    TenantUpdate,
    UsageSummary,
)
from app.schemas.common import ErrorResponse
from app.services.api_key_service import ApiKeyService

router = APIRouter(dependencies=[Depends(require_admin_session)])
_api_key_service = ApiKeyService()


# ---------------------------------------------------------------------------
# Phase 2B Commit 11 — P0-9 / P0-10: reject client-supplied IDs
# ---------------------------------------------------------------------------


async def _reject_client_supplied_field(request: Request, field: str) -> None:
    """Raise 400 if the request JSON body carries ``field``.

    FastAPI caches ``await request.body()``, so reading it here is safe
    even though the same body is parsed again by the Pydantic body
    argument on the endpoint itself.
    """
    raw = await request.body()
    if not raw:
        return
    import json
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return  # Pydantic will surface the JSON error
    if isinstance(data, dict) and field in data:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "client_supplied_id_forbidden",
                    "message": (
                        f"client-supplied {field} is not permitted; "
                        "server generates identifiers"
                    ),
                    "field": field,
                }
            },
        )


async def _reject_tenant_id_on_body(request: Request) -> None:
    await _reject_client_supplied_field(request, "tenant_id")


async def _reject_project_id_on_body(request: Request) -> None:
    await _reject_client_supplied_field(request, "project_id")

# Stage 7: DLP admin routes (detectors / policy profiles / NER settings).
# Mounted on the same router so they inherit require_admin.
from app.api.routes.admin_dlp import router as _admin_dlp_router  # noqa: E402
router.include_router(_admin_dlp_router)

# AI Sensitive Data Reviewer — mounted on the admin router so it
# inherits require_admin + the /admin prefix.
from app.api.routes.admin_ai_review import router as _admin_ai_review_router  # noqa: E402
router.include_router(_admin_ai_review_router)

# Operational dashboard aggregations (/admin/dashboard/{overview,volume,
# top-models,recent-requests}). Mounted here so it inherits require_admin
# and the /admin prefix. The legacy /admin/dashboard endpoint below stays
# for the existing minimal layout.
from app.api.routes.admin_dashboard import router as _admin_dashboard_router  # noqa: E402
router.include_router(_admin_dashboard_router)


# ---------------------------------------------------------------------------
# Existing hardcoded endpoints (providers, policies, requests)
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def dashboard_stats(
    session: Session = Depends(get_db_session),
):
    """Aggregated dashboard data.

    Uses a plain ``def`` handler so FastAPI runs it in a thread automatically.
    This avoids asyncio.to_thread() which can exhaust the default thread pool
    and freeze the entire process including /health.
    """
    from app.services.providers.model_registry import get_all_candidates
    from collections import defaultdict

    t0 = time.time()

    candidates = get_all_candidates()
    by_provider: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        by_provider[c.provider].append(c.model)

    def _safe_count(label, model_cls, *filters):
        try:
            stmt = sa.select(sa.func.count()).select_from(model_cls)
            for f in filters:
                stmt = stmt.where(f)
            result = session.execute(stmt).scalar() or 0
            return result
        except Exception as e:
            print(f"[dashboard] {label}: ERROR {e}")
            return None

    tenant_count = _safe_count(
        "tenants", TenantORM, TenantORM.status == "active",
    )
    project_count = _safe_count("projects", ProjectORM)
    request_count = _safe_count(
        "requests", RequestLogORM,
        RequestLogORM.created_at >= datetime.utcnow() - timedelta(hours=24),
    )

    total_ms = int((time.time() - t0) * 1000)
    if total_ms > 500:
        print(f"[dashboard] slow: {total_ms}ms")

    return {
        "tenants": tenant_count,
        "projects": project_count,
        "models": len(candidates),
        "providers": len(by_provider),
        "requests": request_count or 0,
        "health": {
            "gateway_api": "ok",
            "dlp_engine": "ok",
            "routing_engine": "ok",
            "provider_adapters": "ok",
        },
    }


@router.get("/providers")
def providers(
    session: Session = Depends(get_db_session),
):
    from app.services.providers.model_registry import get_all_candidates
    from app.services.provider_key_service import ProviderKeyService
    from collections import defaultdict

    by_provider: dict[str, list[str]] = defaultdict(list)
    for c in get_all_candidates():
        by_provider[c.provider].append(c.model)

    svc = ProviderKeyService()
    items = []
    for p, m in sorted(by_provider.items()):
        status = svc.get_key_status(session, p)
        items.append({
            "provider_name": p,
            "enabled": True,
            "models": m,
            "key_count": status["total_keys"],
            "has_valid_key": status["configured"],
        })
    return {"items": items}


@router.put("/providers/{provider_name}/key")
def set_provider_key(
    provider_name: str,
    body: dict,
    session: Session = Depends(get_db_session),
):
    """Set or update the API key for a provider."""
    from app.services.provider_key_service import ProviderKeyService

    api_key = body.get("api_key", "").strip()
    if not api_key:
        from app.core.exceptions import ValidationError
        raise ValidationError("api_key is required")

    svc = ProviderKeyService()
    svc.set_api_key(session, provider_name, api_key)
    return {"provider_name": provider_name, "key_set": True}


@router.get("/providers/{provider_name}/key/status")
def provider_key_status(
    provider_name: str,
    session: Session = Depends(get_db_session),
):
    """Check whether an API key is configured for this provider."""
    from app.services.provider_key_service import ProviderKeyService

    svc = ProviderKeyService()
    return svc.get_key_status(session, provider_name)


@router.get("/providers/{provider_name}/keys")
def list_provider_keys(
    provider_name: str,
    session: Session = Depends(get_db_session),
):
    """List all keys for a provider (masked, with status)."""
    from app.services.provider_key_service import ProviderKeyService
    svc = ProviderKeyService()
    keys = svc.list_keys(session, provider_name)
    return {"items": keys}


@router.post("/providers/{provider_name}/keys")
async def add_provider_key(
    provider_name: str,
    body: dict,
    session: Session = Depends(get_db_session),
):
    """Add a new API key — validates against the real API before saving."""
    from app.services.provider_key_service import ProviderKeyService

    api_key = body.get("api_key", "").strip()
    label = body.get("label", "").strip() or None
    if not api_key:
        from app.core.exceptions import ValidationError
        raise ValidationError("api_key is required")

    svc = ProviderKeyService()

    # Validate key against real API
    test_result = await svc.test_key(provider_name, api_key)
    if not test_result["valid"]:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "key_validation_failed",
                    "message": f"Key validation failed: {test_result['error']}",
                },
                "test_result": test_result,
            },
        )

    row = svc.add_key(session, provider_name, api_key, label=label)
    svc.update_test_result(session, row.id, "valid", test_result["latency_ms"])

    return {
        "id": row.id,
        "label": row.label,
        "valid": True,
        "latency_ms": test_result["latency_ms"],
    }


@router.delete("/providers/{provider_name}/keys/{key_id}")
def delete_provider_key(
    provider_name: str,
    key_id: str,
    session: Session = Depends(get_db_session),
):
    """Soft-delete a provider key."""
    from app.services.provider_key_service import ProviderKeyService
    svc = ProviderKeyService()
    svc.delete_key(session, key_id)
    return {"deleted": True}


@router.post("/providers/{provider_name}/keys/{key_id}/test")
async def test_provider_key(
    provider_name: str,
    key_id: str,
    session: Session = Depends(get_db_session),
):
    """Re-test an existing key and update its status."""
    from app.services.provider_key_service import ProviderKeyService, _decode

    row = session.get(ProviderKeyORM, key_id)
    if row is None:
        from app.core.exceptions import NotFoundError
        raise NotFoundError(f"Key '{key_id}' not found")

    svc = ProviderKeyService()
    raw_key = _decode(row.api_key_encrypted)
    result = await svc.test_key(provider_name, raw_key)
    svc.update_test_result(session, key_id, "valid" if result["valid"] else "invalid", result.get("latency_ms"))

    return result


@router.get("/providers/{provider_name}/health")
async def provider_health(provider_name: str):
    """On-demand health check — pings the external API with a 10s timeout."""
    import asyncio
    import httpx
    from app.core.config import settings

    endpoints = {
        "openai": ("https://api.openai.com/v1/models", settings.openai_api_key),
        "anthropic": ("https://api.anthropic.com/v1/messages", settings.anthropic_api_key),
        "openrouter": ("https://openrouter.ai/api/v1/models", settings.openrouter_api_key),
    }

    entry = endpoints.get(provider_name)
    if not entry:
        return {"provider": provider_name, "status": "unknown", "message": "No health check configured"}

    url, api_key = entry
    if not api_key:
        return {"provider": provider_name, "status": "no_key", "message": "API key not configured"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {api_key}"}
            if provider_name == "anthropic":
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
            resp = await client.get(url, headers=headers)
            return {
                "provider": provider_name,
                "status": "ok" if resp.status_code < 400 else "error",
                "http_status": resp.status_code,
                "latency_ms": round(resp.elapsed.total_seconds() * 1000),
            }
    except Exception as e:
        return {"provider": provider_name, "status": "unreachable", "message": str(e)[:200]}


@router.get("/models", response_model=AdminListResponse[ModelDetail])
def list_models() -> AdminListResponse[ModelDetail]:
    from app.services.providers.model_registry import get_all_candidates

    candidates = get_all_candidates()
    return AdminListResponse(
        items=[
            ModelDetail(
                model=c.model,
                provider=c.provider,
                supports_streaming=c.supports_streaming,
                cost_tier=c.cost_tier,
                latency_tier=c.latency_tier,
            )
            for c in candidates
        ]
    )


@router.get("/policies")
def policies(
    session: Session = Depends(get_db_session),
):
    """List all policies — built-in + DB-stored."""
    import json
    from app.models.policy import PolicyORM

    # Built-in profiles. ``mode`` mirrors ``action`` — it is the contract
    # field that response_contracts tests assert on.
    builtins = [
        {"profile_id": "default", "name": "Default", "action": "allow", "mode": "allow", "rules": []},
        {"profile_id": "strict", "name": "Strict", "action": "mask", "mode": "mask", "rules": []},
    ]

    # DB policies
    rows = session.execute(select(PolicyORM).order_by(PolicyORM.created_at)).scalars().all()
    db_ids = set()
    db_items = []
    for r in rows:
        db_ids.add(r.profile_id)
        db_items.append({
            "profile_id": r.profile_id,
            "name": r.name,
            "action": r.action,
            "mode": r.action,
            "rules": json.loads(r.rules_json) if r.rules_json else [],
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    # Merge: DB overrides built-in if same profile_id
    items = [b for b in builtins if b["profile_id"] not in db_ids] + db_items
    return {"items": items}


@router.post("/policies", status_code=201)
def create_policy(
    body: PolicyCreate,
    session: Session = Depends(get_db_session),
):
    import json
    from app.models.policy import PolicyORM
    from app.core.exceptions import ConflictError

    existing = session.get(PolicyORM, body.profile_id)
    if existing is not None:
        raise ConflictError(f"Policy '{body.profile_id}' already exists.")

    row = PolicyORM(
        profile_id=body.profile_id,
        name=body.name,
        action=body.action,
        rules_json=json.dumps(body.rules),
    )
    session.add(row)
    session.flush()
    return {
        "profile_id": row.profile_id,
        "name": row.name,
        "action": row.action,
        "rules": body.rules,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.put("/policies/{profile_id}")
def update_policy(
    profile_id: str,
    body: PolicyUpdate,
    session: Session = Depends(get_db_session),
):
    import json
    from app.models.policy import PolicyORM

    row = session.get(PolicyORM, profile_id)
    if row is None:
        raise NotFoundError(f"Policy '{profile_id}' not found.")
    if body.name is not None:
        row.name = body.name
    if body.action is not None:
        row.action = body.action
    if body.rules is not None:
        row.rules_json = json.dumps(body.rules)
    session.flush()
    return {
        "profile_id": row.profile_id,
        "name": row.name,
        "action": row.action,
        "rules": json.loads(row.rules_json) if row.rules_json else [],
    }


@router.delete("/policies/{profile_id}", status_code=204)
def delete_policy(
    profile_id: str,
    session: Session = Depends(get_db_session),
) -> None:
    from app.models.policy import PolicyORM

    row = session.get(PolicyORM, profile_id)
    if row is None:
        raise NotFoundError(f"Policy '{profile_id}' not found.")
    session.delete(row)
    session.flush()


def _strip_raw_from_findings(findings: list[dict]) -> list[dict]:
    """Drop ``matched_text`` from every finding entry.

    ``matched_text_len`` is preserved (and synthesized from any surviving
    ``matched_text`` if missing on legacy rows) so non-admin viewers can
    still see *that* a value was masked and roughly how long it was —
    just not the value itself.
    """
    cleaned: list[dict] = []
    for entry in findings:
        if not isinstance(entry, dict):
            continue
        copy = dict(entry)
        # Backfill matched_text_len for rows written before Fix 4c so the
        # field stays useful on historical data.
        if "matched_text_len" not in copy and isinstance(copy.get("matched_text"), str):
            copy["matched_text_len"] = len(copy["matched_text"])
        copy.pop("matched_text", None)
        cleaned.append(copy)
    return cleaned


@router.get("/requests/{request_id}", response_model=RequestLogDetail)
def get_request(
    request_id: str,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> RequestLogDetail:
    import json as _json

    from app.services.request_log_service import RequestLogService

    svc = RequestLogService()
    r = svc.get_by_id(session, request_id)
    if r is None:
        raise NotFoundError(f"Request '{request_id}' not found.")
    check_tenant_access(auth, r.tenant_id)

    # Privacy gate (Fix 4a): only ADMIN-tier callers see the raw-content
    # columns and the raw ``matched_text`` inside dlp_findings. Lower
    # admin tiers (TENANT_ADMIN, PROJECT_ADMIN) still get the full
    # masked snapshots + the per-finding metadata, just without the
    # actual sensitive values. ``Role.ADMIN`` is the highest tier in
    # the current RBAC enum (see backend/app/domain/rbac.py); when a
    # SUPER_ADMIN or COMPLIANCE_ADMIN role is added later, this is the
    # one place to widen.
    try:
        caller_role = Role[auth.role.upper()]
    except (KeyError, AttributeError):
        caller_role = Role.USER
    include_raw = has_role_level(caller_role, Role.ADMIN)

    # Parse DLP findings JSON for the trace tab. Treat anything that
    # doesn't decode to a list as None — operators see the raw text
    # snapshots either way, and a corrupt findings blob shouldn't
    # 500 the detail endpoint.
    dlp_findings: list[dict] | None = None
    if r.dlp_findings_json:
        try:
            parsed = _json.loads(r.dlp_findings_json)
            if isinstance(parsed, list):
                dlp_findings = parsed
        except Exception:
            dlp_findings = None

    if dlp_findings is not None and not include_raw:
        dlp_findings = _strip_raw_from_findings(dlp_findings)

    return RequestLogDetail(
        request_id=r.request_id,
        tenant_id=r.tenant_id,
        project_id=r.project_id,
        provider=r.provider,
        model=r.model,
        status=r.status,
        policy_action=r.policy_action,
        policy_profile_id=r.policy_profile_id,
        latency_ms=r.latency_ms,
        prompt_tokens=r.prompt_tokens,
        completion_tokens=r.completion_tokens,
        total_tokens=r.total_tokens,
        estimated_cost_usd=r.estimated_cost_usd,
        error_code=r.error_code,
        created_at=r.created_at,
        # Fix 4a: raw snapshots gated behind ADMIN role. The masked /
        # final snapshots are always returned — they contain only mask
        # tokens and post-restore values that the user already saw.
        input_raw=r.input_raw if include_raw else None,
        input_masked=r.input_masked,
        output_raw=r.output_raw if include_raw else None,
        output_final=r.output_final,
        dlp_findings=dlp_findings,
    )


@router.get("/requests")
def requests(
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
):
    """Paginated request log listing.

    ``limit`` is capped at 5000 — the audit UI's CSV export bumps it to
    1000, and holding the line below 5000 keeps a single response under
    a few megabytes and under the gunicorn timeout even on cold caches.
    ``total`` reflects the filtered set regardless of pagination.
    """
    from app.services.request_log_service import RequestLogService

    try:
        svc = RequestLogService()
        rows, total = svc.list_paginated(
            session,
            limit=limit,
            offset=offset,
            project_id=project_id,
        )
        return {
            "items": [
                {
                    "request_id": r.request_id,
                    "provider": r.provider,
                    "model": r.model,
                    "status": r.status,
                    "policy_action": r.policy_action,
                    "policy_profile_id": r.policy_profile_id,
                    "latency_ms": r.latency_ms,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "estimated_cost_usd": r.estimated_cost_usd,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        print(f"[requests] ERROR: {e}")
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "error": "query_failed",
        }


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


@router.get("/usage/summary", response_model=UsageSummary)
def usage_summary(
    project_id: str | None = None,
    session: Session = Depends(get_db_session),
) -> UsageSummary:
    """Return usage totals for a project, or across all projects.

    - With ``project_id`` → per-project aggregate (unchanged behavior).
    - Without ``project_id`` → aggregate across every project via
      ``usage_summary_all``. ``project_id`` in the response is ``None``
      so clients can tell the two modes apart.
    """
    from app.services.request_log_service import RequestLogService

    svc = RequestLogService()
    if project_id is None:
        data = svc.usage_summary_all(session)
    else:
        data = svc.usage_summary_by_project(session, project_id)
    return UsageSummary(**data)


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------


def _tenant_to_response(tenant: TenantORM) -> TenantResponse:
    return TenantResponse(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        region=tenant.region,
        ner_enabled=bool(getattr(tenant, "ner_enabled", False)),
        created_at=tenant.created_at,
    )


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: str,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> TenantResponse:
    check_tenant_access(auth, tenant_id)
    tenant = session.get(TenantORM, tenant_id)
    if tenant is None:
        raise NotFoundError(f"Tenant '{tenant_id}' not found.")
    return _tenant_to_response(tenant)


@router.get("/tenants", response_model=AdminListResponse[TenantResponse])
def list_tenants(
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_db_session),
) -> AdminListResponse[TenantResponse]:
    total = session.execute(select(sa.func.count()).select_from(TenantORM)).scalar()
    rows = session.execute(select(TenantORM).offset(offset).limit(limit)).scalars().all()
    return AdminListResponse(
        items=[_tenant_to_response(t) for t in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=201,
    dependencies=[Depends(_reject_tenant_id_on_body)],
)
def create_tenant(
    body: TenantCreate,
    session: Session = Depends(get_db_session),
) -> TenantResponse:
    tenant = TenantORM(
        tenant_id=str(uuid.uuid4()),
        name=body.name,
        slug=body.slug,
        region=body.region,
        ner_enabled=body.ner_enabled,
    )
    session.add(tenant)
    session.flush()
    return _tenant_to_response(tenant)


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: str,
    body: TenantUpdate,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> TenantResponse:
    check_tenant_access(auth, tenant_id)
    tenant = session.get(TenantORM, tenant_id)
    if tenant is None:
        raise NotFoundError(f"Tenant '{tenant_id}' not found.")
    if body.name is not None:
        tenant.name = body.name
    if body.region is not None:
        tenant.region = body.region
    if body.status is not None:
        tenant.status = body.status
    if body.ner_enabled is not None:
        tenant.ner_enabled = body.ner_enabled
    session.flush()
    return _tenant_to_response(tenant)


@router.delete("/tenants/{tenant_id}", status_code=204)
def delete_tenant(
    tenant_id: str,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> None:
    check_tenant_access(auth, tenant_id)
    tenant = session.get(TenantORM, tenant_id)
    if tenant is None:
        raise NotFoundError(f"Tenant '{tenant_id}' not found.")
    tenant.status = "deleted"
    session.flush()


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> ProjectResponse:
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project '{project_id}' not found.")
    check_tenant_access(auth, project.tenant_id)
    return ProjectResponse(
        project_id=project.project_id,
        tenant_id=project.tenant_id,
        name=project.name,
        policy_profile_id=project.policy_profile_id,
        created_at=project.created_at,
    )


@router.get("/projects", response_model=AdminListResponse[ProjectResponse])
def list_projects(
    tenant_id: str | None = None,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> AdminListResponse[ProjectResponse]:
    # P0-5 fix: tenant-scoped callers only see their own tenant's
    # projects. Global ADMIN honours the query param (or, if omitted,
    # returns every project).
    from app.domain.rbac import Role
    try:
        caller_role = Role[auth.role.upper()]
    except KeyError:
        caller_role = Role.USER
    if caller_role < Role.ADMIN:
        # Force-override any caller-supplied tenant_id. A tenant-scoped
        # admin cannot peek into another tenant's data regardless of the
        # query parameter.
        tenant_id = auth.tenant_id

    stmt = select(ProjectORM)
    if tenant_id:
        stmt = stmt.where(ProjectORM.tenant_id == tenant_id)
    rows = session.execute(stmt).scalars().all()
    return AdminListResponse(
        items=[
            ProjectResponse(
                project_id=p.project_id,
                tenant_id=p.tenant_id,
                name=p.name,
                policy_profile_id=p.policy_profile_id,
                created_at=p.created_at,
            )
            for p in rows
        ]
    )


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=201,
    dependencies=[Depends(_reject_project_id_on_body)],
)
def create_project(
    body: ProjectCreate,
    session: Session = Depends(get_db_session),
) -> ProjectResponse:
    # Verify tenant exists
    tenant = session.get(TenantORM, body.tenant_id)
    if tenant is None:
        raise NotFoundError(
            f"Tenant '{body.tenant_id}' not found.",
            details={"tenant_id": body.tenant_id},
        )

    project = ProjectORM(
        project_id=str(uuid.uuid4()),
        tenant_id=body.tenant_id,
        name=body.name,
        policy_profile_id=body.policy_profile_id,
    )
    session.add(project)
    session.flush()
    return ProjectResponse(
        project_id=project.project_id,
        tenant_id=project.tenant_id,
        name=project.name,
        policy_profile_id=project.policy_profile_id,
        created_at=project.created_at,
    )


@router.put("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    body: ProjectUpdate,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> ProjectResponse:
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project '{project_id}' not found.")
    check_tenant_access(auth, project.tenant_id)
    if body.name is not None:
        project.name = body.name
    if body.policy_profile_id is not None:
        project.policy_profile_id = body.policy_profile_id
    session.flush()
    return ProjectResponse(
        project_id=project.project_id,
        tenant_id=project.tenant_id,
        name=project.name,
        policy_profile_id=project.policy_profile_id,
        created_at=project.created_at,
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> None:
    project = session.get(ProjectORM, project_id)
    if project is None:
        raise NotFoundError(f"Project '{project_id}' not found.")
    check_tenant_access(auth, project.tenant_id)
    session.delete(project)
    session.flush()


# ---------------------------------------------------------------------------
# API Key CRUD
# ---------------------------------------------------------------------------


@router.get("/api-keys/{key_id}", response_model=ApiKeyResponse)
def get_api_key(
    key_id: str,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> ApiKeyResponse:
    api_key = session.get(ApiKeyORM, key_id)
    if api_key is None:
        raise NotFoundError(f"API key '{key_id}' not found.")
    check_tenant_access(auth, api_key.tenant_id)
    return ApiKeyResponse(
        key_id=api_key.key_id,
        key_prefix=api_key.key_prefix,
        tenant_id=api_key.tenant_id,
        project_id=api_key.project_id,
        name=api_key.name,
        status=api_key.status,
        created_at=api_key.created_at,
    )


@router.get("/api-keys", response_model=AdminListResponse[ApiKeyResponse])
def list_api_keys(
    project_id: str | None = None,
    session: Session = Depends(get_db_session),
    auth: ResolvedApiKey = Depends(require_admin_session),
) -> AdminListResponse[ApiKeyResponse]:
    # P0-6 fix: tenant-scoped callers only see their own tenant's keys.
    # If a project_id is supplied, we additionally verify that project
    # belongs to the caller's tenant before returning its keys.
    from app.domain.rbac import Role
    try:
        caller_role = Role[auth.role.upper()]
    except KeyError:
        caller_role = Role.USER

    stmt = select(ApiKeyORM)
    if caller_role < Role.ADMIN:
        # Force-scope to caller's tenant. A forged project_id that
        # belongs to another tenant will drop to zero rows below.
        stmt = stmt.where(ApiKeyORM.tenant_id == auth.tenant_id)
    if project_id:
        stmt = stmt.where(ApiKeyORM.project_id == project_id)
    rows = session.execute(stmt).scalars().all()
    return AdminListResponse(
        items=[
            ApiKeyResponse(
                key_id=k.key_id,
                key_prefix=k.key_prefix,
                tenant_id=k.tenant_id,
                project_id=k.project_id,
                name=k.name,
                status=k.status,
                created_at=k.created_at,
            )
            for k in rows
        ]
    )


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=201,
)
def create_api_key(
    body: ApiKeyCreate,
    session: Session = Depends(get_db_session),
    auth: object = Depends(require_admin_session),
) -> ApiKeyCreateResponse:
    raw_key, api_key = _api_key_service.create_key(
        session,
        tenant_id=body.tenant_id,
        project_id=body.project_id,
        name=body.name,
    )
    # Audit trail for sensitive action
    from app.services.admin_audit_service import AdminAuditService
    AdminAuditService().record(
        session,
        actor_key_id=getattr(auth, "key_id", "unknown"),
        tenant_id=body.tenant_id,
        action="key_create",
        resource_type="api_key",
        resource_id=api_key.key_id,
    )
    return ApiKeyCreateResponse(
        key_id=api_key.key_id,
        key_prefix=api_key.key_prefix,
        key=raw_key,
        tenant_id=api_key.tenant_id,
        project_id=api_key.project_id,
        name=api_key.name,
        status=api_key.status,
        created_at=api_key.created_at,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str,
    session: Session = Depends(get_db_session),
    auth: object = Depends(require_admin_session),
) -> None:
    # Look up key before revoke for audit context
    api_key = session.get(ApiKeyORM, key_id)
    revoked = _api_key_service.revoke_key(session, key_id)
    if not revoked:
        raise NotFoundError(
            f"API key '{key_id}' not found.",
            details={"key_id": key_id},
        )
    # Audit trail
    from app.services.admin_audit_service import AdminAuditService
    AdminAuditService().record(
        session,
        actor_key_id=getattr(auth, "key_id", "unknown"),
        tenant_id=api_key.tenant_id if api_key else "unknown",
        action="key_revoke",
        resource_type="api_key",
        resource_id=key_id,
    )


# ---------------------------------------------------------------------------
# Alert rule CRUD
# ---------------------------------------------------------------------------


def _alert_to_response(row: AlertRuleORM) -> AlertRuleResponse:
    return AlertRuleResponse(
        id=row.id,
        name=row.name,
        metric=row.metric,
        threshold=row.threshold,
        window_minutes=row.window_minutes,
        scope=row.scope,
        scope_id=row.scope_id,
        enabled=row.enabled,
        webhook_url=row.webhook_url,
        last_fired_at=row.last_fired_at,
        created_at=row.created_at,
    )


def _validate_alert_rule(metric: str | None, scope: str | None) -> None:
    from app.core.exceptions import ValidationError

    if metric is not None and metric not in ALERT_METRICS:
        raise ValidationError(
            f"Unknown metric '{metric}'.",
            details={"allowed": list(ALERT_METRICS)},
        )
    if scope is not None and scope not in ALERT_SCOPES:
        raise ValidationError(
            f"Unknown scope '{scope}'.",
            details={"allowed": list(ALERT_SCOPES)},
        )


@router.get("/alerts", response_model=AdminListResponse[AlertRuleResponse])
def list_alerts(
    session: Session = Depends(get_db_session),
) -> AdminListResponse[AlertRuleResponse]:
    rows = session.execute(
        select(AlertRuleORM).order_by(AlertRuleORM.created_at)
    ).scalars().all()
    return AdminListResponse(items=[_alert_to_response(r) for r in rows])


@router.post("/alerts", response_model=AlertRuleResponse, status_code=201)
def create_alert(
    body: AlertRuleCreate,
    session: Session = Depends(get_db_session),
) -> AlertRuleResponse:
    _validate_alert_rule(body.metric, body.scope)
    row = AlertRuleORM(
        id=str(uuid.uuid4()),
        name=body.name,
        metric=body.metric,
        threshold=body.threshold,
        window_minutes=body.window_minutes,
        scope=body.scope,
        scope_id=body.scope_id,
        enabled=body.enabled,
        webhook_url=body.webhook_url,
    )
    session.add(row)
    session.flush()
    return _alert_to_response(row)


@router.get("/alerts/{alert_id}", response_model=AlertRuleResponse)
def get_alert(
    alert_id: str,
    session: Session = Depends(get_db_session),
) -> AlertRuleResponse:
    row = session.get(AlertRuleORM, alert_id)
    if row is None:
        raise NotFoundError(f"Alert rule '{alert_id}' not found.")
    return _alert_to_response(row)


@router.put("/alerts/{alert_id}", response_model=AlertRuleResponse)
def update_alert(
    alert_id: str,
    body: AlertRuleUpdate,
    session: Session = Depends(get_db_session),
) -> AlertRuleResponse:
    row = session.get(AlertRuleORM, alert_id)
    if row is None:
        raise NotFoundError(f"Alert rule '{alert_id}' not found.")
    _validate_alert_rule(body.metric, body.scope)
    if body.name is not None:
        row.name = body.name
    if body.metric is not None:
        row.metric = body.metric
    if body.threshold is not None:
        row.threshold = body.threshold
    if body.window_minutes is not None:
        row.window_minutes = body.window_minutes
    if body.scope is not None:
        row.scope = body.scope
    if body.scope_id is not None:
        row.scope_id = body.scope_id
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.webhook_url is not None:
        row.webhook_url = body.webhook_url
    session.flush()
    return _alert_to_response(row)


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(
    alert_id: str,
    session: Session = Depends(get_db_session),
) -> None:
    row = session.get(AlertRuleORM, alert_id)
    if row is None:
        raise NotFoundError(f"Alert rule '{alert_id}' not found.")
    session.delete(row)
    session.flush()


# ---------------------------------------------------------------------------
# Alert events — read-only audit trail of fired alerts
# ---------------------------------------------------------------------------


def _alert_event_to_response(row: AlertEventORM) -> AlertEventResponse:
    return AlertEventResponse(
        id=row.id,
        rule_id=row.rule_id,
        alert_type=row.alert_type,
        severity=row.severity,
        message=row.message,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        delivery_status=row.delivery_status,
        fired_at=row.fired_at,
    )


@router.get(
    "/alerts/{alert_id}/events",
    response_model=AdminListResponse[AlertEventResponse],
)
def list_events_for_rule(
    alert_id: str,
    session: Session = Depends(get_db_session),
) -> AdminListResponse[AlertEventResponse]:
    """Return the 50 most recent alert_events for a given rule_id."""
    rows = (
        session.execute(
            select(AlertEventORM)
            .where(AlertEventORM.rule_id == alert_id)
            .order_by(AlertEventORM.fired_at.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return AdminListResponse(
        items=[_alert_event_to_response(r) for r in rows]
    )


@router.get(
    "/alert-events",
    response_model=AdminListResponse[AlertEventResponse],
)
def list_alert_events(
    tenant_id: Optional[str] = None,
    limit: int = 50,
    session: Session = Depends(get_db_session),
) -> AdminListResponse[AlertEventResponse]:
    """Return recent alert_events across all rules, newest first.

    Supports optional ``tenant_id`` filter. ``limit`` is capped at 200.
    """
    limit = max(1, min(limit, 200))
    stmt = select(AlertEventORM).order_by(AlertEventORM.fired_at.desc())
    if tenant_id is not None:
        stmt = stmt.where(AlertEventORM.tenant_id == tenant_id)
    rows = session.execute(stmt.limit(limit)).scalars().all()
    return AdminListResponse(
        items=[_alert_event_to_response(r) for r in rows]
    )


# ---------------------------------------------------------------------------
# Keyword rule CRUD
# ---------------------------------------------------------------------------


def _keyword_rule_to_response(row: KeywordRuleORM) -> KeywordRuleResponse:
    from app.services.keyword_rule_service import KeywordRuleService

    return KeywordRuleResponse(
        id=row.id,
        name=row.name,
        keywords=KeywordRuleService.deserialize_keywords(row.keywords_json),
        model_override=row.model_override,
        priority=row.priority,
        scope=row.scope,
        scope_id=row.scope_id,
        enabled=row.enabled,
        created_at=row.created_at,
    )


def _validate_keyword_scope(scope: str | None) -> None:
    from app.core.exceptions import ValidationError

    if scope is not None and scope not in KEYWORD_RULE_SCOPES:
        raise ValidationError(
            f"Unknown scope '{scope}'.",
            details={"allowed": list(KEYWORD_RULE_SCOPES)},
        )


@router.get(
    "/keyword-rules",
    response_model=AdminListResponse[KeywordRuleResponse],
)
def list_keyword_rules(
    session: Session = Depends(get_db_session),
) -> AdminListResponse[KeywordRuleResponse]:
    rows = session.execute(
        select(KeywordRuleORM).order_by(KeywordRuleORM.created_at)
    ).scalars().all()
    return AdminListResponse(
        items=[_keyword_rule_to_response(r) for r in rows]
    )


# Alias: part of the "routing insights" group in the admin API.
@router.get(
    "/routing/keyword-rules",
    response_model=AdminListResponse[KeywordRuleResponse],
)
def list_keyword_rules_alias(
    session: Session = Depends(get_db_session),
) -> AdminListResponse[KeywordRuleResponse]:
    return list_keyword_rules(session=session)


@router.post(
    "/keyword-rules",
    response_model=KeywordRuleResponse,
    status_code=201,
)
def create_keyword_rule(
    body: KeywordRuleCreate,
    session: Session = Depends(get_db_session),
) -> KeywordRuleResponse:
    from app.services.keyword_rule_service import KeywordRuleService

    _validate_keyword_scope(body.scope)
    row = KeywordRuleORM(
        id=str(uuid.uuid4()),
        name=body.name,
        keywords_json=KeywordRuleService.serialize_keywords(body.keywords),
        model_override=body.model_override,
        priority=body.priority,
        scope=body.scope,
        scope_id=body.scope_id,
        enabled=body.enabled,
    )
    session.add(row)
    session.flush()
    return _keyword_rule_to_response(row)


@router.put(
    "/keyword-rules/{rule_id}",
    response_model=KeywordRuleResponse,
)
def update_keyword_rule(
    rule_id: str,
    body: KeywordRuleUpdate,
    session: Session = Depends(get_db_session),
) -> KeywordRuleResponse:
    from app.services.keyword_rule_service import KeywordRuleService

    row = session.get(KeywordRuleORM, rule_id)
    if row is None:
        raise NotFoundError(f"Keyword rule '{rule_id}' not found.")
    _validate_keyword_scope(body.scope)
    if body.name is not None:
        row.name = body.name
    if body.keywords is not None:
        row.keywords_json = KeywordRuleService.serialize_keywords(body.keywords)
    if body.model_override is not None:
        row.model_override = body.model_override
    if body.priority is not None:
        row.priority = body.priority
    if body.scope is not None:
        row.scope = body.scope
    if body.scope_id is not None:
        row.scope_id = body.scope_id
    if body.enabled is not None:
        row.enabled = body.enabled
    session.flush()
    return _keyword_rule_to_response(row)


@router.delete("/keyword-rules/{rule_id}", status_code=204)
def delete_keyword_rule(
    rule_id: str,
    session: Session = Depends(get_db_session),
) -> None:
    row = session.get(KeywordRuleORM, rule_id)
    if row is None:
        raise NotFoundError(f"Keyword rule '{rule_id}' not found.")
    session.delete(row)
    session.flush()


# ---------------------------------------------------------------------------
# Routing stats
# ---------------------------------------------------------------------------


@router.get("/routing/stats", response_model=RoutingStatsResponse)
def routing_stats(
    session: Session = Depends(get_db_session),
) -> RoutingStatsResponse:
    """Breakdown of model usage and router wins over the last 24 hours."""
    window_start = datetime.utcnow() - timedelta(hours=24)

    model_rows = session.execute(
        sa.select(
            RequestLogORM.model,
            sa.func.count().label("count"),
        )
        .where(RequestLogORM.created_at >= window_start)
        .group_by(RequestLogORM.model)
        .order_by(sa.desc("count"))
    ).all()
    models = [
        {"model": m or "unknown", "count": int(c)}
        for m, c in model_rows
    ]

    router_rows = session.execute(
        sa.select(
            RequestLogORM.router_used,
            sa.func.count().label("count"),
        )
        .where(
            RequestLogORM.created_at >= window_start,
            RequestLogORM.router_used.is_not(None),
        )
        .group_by(RequestLogORM.router_used)
        .order_by(sa.desc("count"))
    ).all()
    routers = [
        {"router": r, "count": int(c)}
        for r, c in router_rows
    ]

    return RoutingStatsResponse(models=models, routers=routers)
