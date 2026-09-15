"""FIX120 — admin view and control of the fallback policy.

Read:
  GET  /admin/fallback/policy            the shipped policy, the kill switch,
                                          the stored overrides, the effective
                                          policy (``?tenant_id=`` for a tenant)
  GET  /admin/fallback/chains            every chat model with its chain (or
                                          none) + counts; ``?tenant_id=`` shows
                                          the chains under that tenant's override
Write (each takes effect on the next turn — no deploy):
  PUT  /admin/fallback/kill-switch       {"enabled": false} stops every chain
                                          walk; {"enabled": true} restores
  PUT  /admin/fallback/overrides/deployment      {"override": {...}}
  DELETE /admin/fallback/overrides/deployment
  PUT  /admin/fallback/overrides/tenant/{id}     {"override": {...}}
  DELETE /admin/fallback/overrides/tenant/{id}

Overrides are validated by ``fallback_policy.validate_override`` (only the
file's ``overridable`` keys; never a ``hard_forbidden`` row; chains must be
well-formed and name models the registry knows). Every write is recorded in
the admin audit log.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_admin_session
from app.core.exceptions import ValidationError
from app.models.system_setting import SystemSettingORM
from app.services.admin_audit_service import AdminAuditService
from app.services.gateway import fallback_policy as fp


router = APIRouter()
_audit = AdminAuditService()


class KillSwitchUpdate(BaseModel):
    enabled: bool


class OverrideUpdate(BaseModel):
    override: dict[str, Any]


def _enabled_from_value(raw: str | None) -> bool:
    if raw is None:
        return bool(fp.base_policy()["kill_switch"].get("default", True))
    return raw.strip().lower() not in ("false", "0", "no", "off")


def _upsert(session: Session, key: str, value: str, description: str) -> SystemSettingORM:
    row = session.get(SystemSettingORM, key)
    if row is None:
        row = SystemSettingORM(key=key, value=value, description=description)
        session.add(row)
    else:
        row.value = value
    session.flush()
    return row


def _stored_overrides(session: Session) -> tuple[dict | None, list[dict]]:
    """The deployment override and every tenant override, parsed (a row
    that does not parse is reported with its error, never hidden)."""
    ov = fp.base_policy()["overrides"]
    dep_key, prefix = ov["deployment_setting_key"], ov["tenant_setting_key_prefix"]
    rows = (
        session.execute(
            select(SystemSettingORM).where(
                (SystemSettingORM.key == dep_key) | (SystemSettingORM.key.like(f"{prefix}%"))
            )
        )
        .scalars()
        .all()
    )
    deployment: dict | None = None
    tenants: list[dict] = []
    for r in rows:
        try:
            parsed = json.loads(r.value) if (r.value or "").strip() else None
            valid = fp.validate_override(parsed, is_tenant=r.key != dep_key) if parsed is not None else None
            entry = {"override": valid, "error": None, "updated_at": r.updated_at}
        except Exception as exc:  # noqa: BLE001
            entry = {"override": None, "error": str(exc)[:200], "updated_at": r.updated_at}
        if r.key == dep_key:
            deployment = entry
        else:
            tenants.append({"tenant_id": r.key[len(prefix):], **entry})
    return deployment, tenants


def _known_models() -> set[str]:
    from app.services.providers.model_registry import get_all_candidates

    return {c.model for c in get_all_candidates()}


def _check_chain_models(override: dict) -> None:
    """Every hop (and every chain key) in an override must be a model the
    registry knows — a typo would be a silent no-op chain."""
    known = _known_models()
    from app.services.providers.model_tiers import _alias

    aliases = {_alias(m) for m in known}
    for sub, chains in (override.get("chains") or {}).items():
        for model, hops in chains.items():
            if model not in known and _alias(model) not in aliases:
                raise ValidationError(
                    f"chains.{sub}: '{model}' is not a model in the catalogue.", code="invalid_value"
                )
            for hop in hops:
                if hop["model"] not in known:
                    raise ValidationError(
                        f"chains.{sub}.{model}: hop model '{hop['model']}' is not in the catalogue.",
                        code="invalid_value",
                    )


@router.get("/policy")
def get_policy(
    tenant_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    _auth=Depends(require_admin_session),
):
    base = fp.base_policy()
    key = fp.kill_switch_key()
    row = session.get(SystemSettingORM, key)
    deployment, tenants = _stored_overrides(session)
    tenant_layer = None
    if tenant_id:
        for t in tenants:
            if t["tenant_id"] == tenant_id:
                tenant_layer = t["override"]
    effective = fp.apply_overrides(base, deployment["override"] if deployment else None, tenant_layer)
    packaged = fp.packaged_policy_path().read_bytes()
    return {
        "version": base.get("version"),
        "contract_sha256": hashlib.sha256(packaged).hexdigest(),
        "kill_switch": {
            "setting_key": key,
            "enabled": _enabled_from_value(row.value if row else None),
            "stored_value": row.value if row else None,
            "updated_at": row.updated_at if row else None,
        },
        "actions": base["actions"],
        "decision_table": base["decision_table"],
        "hard_forbidden": base["hard_forbidden"],
        "boundary": base["boundary"],
        "limits": base["limits"],
        "breaker": base["breaker"],
        "cost_cap": base["cost_cap"],
        "opt_in": base["opt_in"],
        "overridable": base["overrides"]["overridable"],
        "deployment_override": deployment,
        "tenant_overrides": tenants,
        "effective": {
            "tenant_id": tenant_id,
            "limits": effective["limits"],
            "breaker": effective["breaker"],
            "cost_cap": effective["cost_cap"],
            "opt_in": effective["opt_in"],
            "allowed_providers": effective.get("allowed_providers"),
            "decision_table_error_code": effective["decision_table"]["error_code"],
        },
    }


@router.get("/chains")
def get_chains(
    tenant_id: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_db_session),
    _auth=Depends(require_admin_session),
):
    base = fp.base_policy()
    deployment, tenants = _stored_overrides(session)
    tenant_layer = None
    if tenant_id:
        for t in tenants:
            if t["tenant_id"] == tenant_id:
                tenant_layer = t["override"]
    effective = fp.apply_overrides(base, deployment["override"] if deployment else None, tenant_layer)
    report = fp.coverage(policy=effective, chat_only=not include_inactive)
    report["tenant_id"] = tenant_id
    ks_row = session.get(SystemSettingORM, fp.kill_switch_key())
    report["kill_switch_enabled"] = _enabled_from_value(ks_row.value if ks_row else None)
    return report


@router.put("/kill-switch")
def set_kill_switch(
    body: KillSwitchUpdate,
    session: Session = Depends(get_db_session),
    auth=Depends(require_admin_session),
):
    key = fp.kill_switch_key()
    row = _upsert(
        session,
        key,
        "true" if body.enabled else "false",
        "FIX120 — model fallback kill switch: false stops every chain walk without a deploy.",
    )
    _audit.record(
        session,
        actor_key_id=auth.key_id,
        tenant_id=auth.tenant_id,
        action="fallback.kill_switch",
        resource_type="system_setting",
        resource_id=key,
        details={"enabled": body.enabled},
    )
    fp.clear_cache()
    return {"setting_key": key, "enabled": body.enabled, "stored_value": row.value, "updated_at": row.updated_at}


def _put_override(session: Session, auth, tenant_id: str | None, override: dict) -> dict:
    try:
        valid = fp.validate_override(override, is_tenant=tenant_id is not None)
    except ValueError as exc:
        raise ValidationError(str(exc), code="invalid_value") from exc
    _check_chain_models(valid)
    key = fp._override_setting_key(tenant_id)
    row = _upsert(
        session,
        key,
        json.dumps(valid, ensure_ascii=False),
        "FIX120 — fallback policy override (JSON); see docs/contracts/fallback_policy.json → overrides.",
    )
    _audit.record(
        session,
        actor_key_id=auth.key_id,
        tenant_id=auth.tenant_id,
        action="fallback.override.set",
        resource_type="system_setting",
        resource_id=key,
        details={"tenant_id": tenant_id, "keys": sorted(valid.keys())},
    )
    fp.clear_cache()
    return {"setting_key": key, "tenant_id": tenant_id, "override": valid, "updated_at": row.updated_at}


def _delete_override(session: Session, auth, tenant_id: str | None) -> dict:
    key = fp._override_setting_key(tenant_id)
    row = session.get(SystemSettingORM, key)
    if row is not None:
        session.delete(row)
        session.flush()
    _audit.record(
        session,
        actor_key_id=auth.key_id,
        tenant_id=auth.tenant_id,
        action="fallback.override.delete",
        resource_type="system_setting",
        resource_id=key,
        details={"tenant_id": tenant_id, "existed": row is not None},
    )
    fp.clear_cache()
    return {"setting_key": key, "tenant_id": tenant_id, "deleted": row is not None}


@router.put("/overrides/deployment")
def put_deployment_override(
    body: OverrideUpdate,
    session: Session = Depends(get_db_session),
    auth=Depends(require_admin_session),
):
    return _put_override(session, auth, None, body.override)


@router.delete("/overrides/deployment")
def delete_deployment_override(
    session: Session = Depends(get_db_session),
    auth=Depends(require_admin_session),
):
    return _delete_override(session, auth, None)


@router.put("/overrides/tenant/{tenant_id}")
def put_tenant_override(
    tenant_id: str,
    body: OverrideUpdate,
    session: Session = Depends(get_db_session),
    auth=Depends(require_admin_session),
):
    return _put_override(session, auth, tenant_id, body.override)


@router.delete("/overrides/tenant/{tenant_id}")
def delete_tenant_override(
    tenant_id: str,
    session: Session = Depends(get_db_session),
    auth=Depends(require_admin_session),
):
    return _delete_override(session, auth, tenant_id)
