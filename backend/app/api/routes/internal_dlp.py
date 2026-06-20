"""Internal DLP endpoints — trusted service-to-service only.

Phase 4A.2.5 (B2): the Go→Vault V2 restore bridge. chat-edge's display
lifecycle calls this to resolve display tokens back to raw values for an
AUTHORIZED owner on the reload path, since chat-edge (Go) cannot reach the
Python/Redis Vault V2 directly.

Security contract (all enforced here, never trusted from the body):
  * Internal-trusted only — the caller MUST prove ``X-Internal-Service-Secret``
    (``auth.internal_trusted``); otherwise 403. Same trust anchor as the rate
    limiter / canonical transport gate.
  * Scope is DERIVED SERVER-SIDE from the authenticated context
    (tenant+project from the API key, actor from the trusted ``X-User-ID``,
    conversation from the trusted ``X-Conversation-ID``) via
    ``build_dlp_request_context`` → ``VaultScope``. The request body carries
    ONLY the token list + consumer type — never scope.
  * Vault V2 must be enabled (``DLP_VAULT_V2_ENABLED``); otherwise NO restore
    (all tokens denied ``vault_disabled``) — never a legacy ConversationVault
    fallback.
  * Only the ``browser_reload_owner`` consumer may restore. Any other consumer
    (admin/export/search/provider) → all denied.
  * Authorization gate (restorable_to_user + not never_restore + category not
    in never-restore set) is applied by Vault V2; secrets / strong identifiers
    / OTP / phone / card never restore even within the correct scope.
  * Wrong scope / wrong actor / wrong conversation / expired / not-found are
    all indistinguishable → denied ``not_found``.
  * Logs carry ONLY safe counts + denial-reason enums — never raw values,
    never the token→value mapping.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import get_resolved_api_key
from app.core.config import settings
from app.services.dlp.policy_engine import (
    build_dlp_request_context,
    ScanDirection,
    SourceType,
)
from app.services.dlp.vault_v2 import (
    VaultScope,
    _NEVER_RESTORE_CATEGORIES,
    conversation_vault_v2,
)
from app.services.dlp.secure_calc import (
    CalcError,
    coerce_operation,
    is_calc_token,
    secure_calc_result_store,
    secure_calculate,
)

router = APIRouter()
logger = structlog.get_logger(__name__)

# The ONLY consumer permitted to restore raw display through this bridge.
_OWNER_RELOAD = "browser_reload_owner"
# The ONLY consumer permitted to invoke the secure calculator.
_CALC_CONSUMER = "managed_webchat"

# Defensive cap so a malformed/abusive request can't ask for an unbounded
# token list. Far above any real assistant turn's token count.
_MAX_TOKENS = 512


class _RestoreRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)
    consumer: str = ""


def _entry_restorable(entry) -> bool:
    """Resolve-time authorization gate (mirrors vault_v2._entry_restorable)."""
    return (
        entry.restorable_to_user is True
        and entry.never_restore is False
        and entry.category not in _NEVER_RESTORE_CATEGORIES
    )


@router.post("/internal/dlp/vault-v2/restore")
async def vault_v2_restore(
    body: _RestoreRequest,
    auth=Depends(get_resolved_api_key),
    x_internal_service_secret: str | None = Header(default=None),
):
    """Authorized owner restore of display tokens via scoped Vault V2.

    Returns ``{"tokens": {tok: {restored, restorable_to_user, never_restore}},
    "denied": {tok: reason}}``. Never raises on an unresolvable token — those
    are simply ``denied`` so the Go caller falls back to the tokenized display.
    """
    # 1. Internal-trusted gate (defence in depth on top of get_resolved_api_key).
    configured = (settings.internal_service_secret or "").strip()
    supplied = (x_internal_service_secret or "").strip()
    if (
        not configured
        or not supplied
        or not hmac.compare_digest(supplied, configured)
        or not bool(getattr(auth, "internal_trusted", False))
    ):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    tokens = [t for t in (body.tokens or []) if isinstance(t, str) and t][:_MAX_TOKENS]

    # 2. Consumer gate — only the owner reload path may restore.
    if body.consumer != _OWNER_RELOAD:
        logger.info("dlp_vault_v2_restore_denied_consumer", consumer=str(body.consumer)[:32])
        return {"tokens": {}, "denied": {t: "consumer_not_allowed" for t in tokens}}

    # 3. Vault V2 must be enabled — never fall back to the legacy vault.
    if not settings.dlp_vault_v2_enabled:
        return {"tokens": {}, "denied": {t: "vault_disabled" for t in tokens}}

    # 4. Scope is derived SERVER-SIDE from the authenticated context.
    ctx = build_dlp_request_context(
        auth=auth,
        direction=ScanDirection.OUTPUT,
        source_type=SourceType.WEBCHAT,
    )
    scope = VaultScope.from_context(ctx)
    if scope is None or not scope.is_valid or not tokens:
        return {"tokens": {}, "denied": {t: "not_found" for t in tokens}}

    # 5. Read scoped entries (HMGET only) and apply the authorization gate.
    #    ``<CALC_n>`` result tokens resolve from the scoped secure-calc store;
    #    ``<rN>`` / ``<<CAT_N>>`` from Vault V2. Both owner-authorized + scoped.
    vault_tokens = [t for t in tokens if not is_calc_token(t)]
    calc_tokens = [t for t in tokens if is_calc_token(t)]
    entries = await conversation_vault_v2.get_entries_scoped_async(scope, vault_tokens)
    calc_restored = (
        await secure_calc_result_store.resolve_for_user_async(scope, calc_tokens)
        if calc_tokens
        else {}
    )
    restored: dict[str, dict] = {}
    denied: dict[str, str] = {}
    for tok in tokens:
        if is_calc_token(tok):
            if tok in calc_restored:
                restored[tok] = {
                    "restored": calc_restored[tok],
                    "restorable_to_user": True,
                    "never_restore": False,
                }
            else:
                denied[tok] = "not_found"
            continue
        entry = entries.get(tok)
        if entry is None:
            denied[tok] = "not_found"
            continue
        if entry.never_restore:
            denied[tok] = "never_restore"
            continue
        if not _entry_restorable(entry):
            denied[tok] = "not_restorable"
            continue
        restored[tok] = {
            "restored": entry.raw_value,
            "restorable_to_user": True,
            "never_restore": False,
        }

    # Safe telemetry only: counts + denial-reason histogram. No raw, no map.
    reasons: dict[str, int] = {}
    for r in denied.values():
        reasons[r] = reasons.get(r, 0) + 1
    logger.info(
        "dlp_vault_v2_restore",
        scope_fp=scope.fingerprint(),
        requested=len(tokens),
        restored=len(restored),
        denied=len(denied),
        denied_reasons=reasons,
    )
    return {"tokens": restored, "denied": denied}


class _CalcRequest(BaseModel):
    operation: str = ""
    operand_tokens: list[str] = Field(default_factory=list)
    consumer: str = ""


def _calc_gate_open() -> bool:
    """Composed server-side gate for the secure calculator (the flags Python
    owns). chat-edge additionally enforces canonical-history + display-lifecycle
    on its side before ever calling this. ALL must be on."""
    return bool(
        settings.dlp_secure_calc_enabled
        and settings.dlp_vault_v2_enabled
        and settings.dlp_policy_engine_v2_enabled
        and settings.dlp_canonical_artifact_enabled
    )


@router.post("/internal/dlp/secure-calc")
async def secure_calc(
    body: _CalcRequest,
    auth=Depends(get_resolved_api_key),
    x_internal_service_secret: str | None = Header(default=None),
):
    """Compute over Vault V2 eligible confidential-numeric tokens → ``<CALC_n>``.

    Returns ``{"result_token": "<CALC_n>", "status": "complete"}`` or
    ``{"status": "denied", "code": <stable enum>}``. NEVER returns a raw operand
    or raw result. Scope is server-derived; Direct API (no internal trust) 403.
    """
    configured = (settings.internal_service_secret or "").strip()
    supplied = (x_internal_service_secret or "").strip()
    if (
        not configured
        or not supplied
        or not hmac.compare_digest(supplied, configured)
        or not bool(getattr(auth, "internal_trusted", False))
    ):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    if body.consumer != _CALC_CONSUMER:
        return JSONResponse(status_code=403, content={"status": "denied", "code": "consumer_not_allowed"})

    if not _calc_gate_open():
        return JSONResponse(status_code=403, content={"status": "denied", "code": "calculator_disabled"})

    operation = coerce_operation(body.operation)
    if operation is None:
        return JSONResponse(status_code=422, content={"status": "denied", "code": "unsupported_operation"})

    tokens = [t for t in (body.operand_tokens or []) if isinstance(t, str) and t][:_MAX_TOKENS]
    if not tokens:
        return JSONResponse(status_code=422, content={"status": "denied", "code": "no_operands"})

    ctx = build_dlp_request_context(
        auth=auth, direction=ScanDirection.OUTPUT, source_type=SourceType.WEBCHAT
    )
    scope = VaultScope.from_context(ctx)
    if scope is None or not scope.is_valid:
        return JSONResponse(status_code=422, content={"status": "denied", "code": "invalid_scope"})

    # Phase 4A.2.7 — tenant-scoped: non-allowlisted tenant/project denied here too.
    from app.services.dlp.feature_scope import is_in_scope as _scope
    if not _scope(getattr(ctx, "tenant_id", None), getattr(ctx, "project_id", None)):
        return JSONResponse(status_code=403, content={"status": "denied", "code": "not_in_scope"})

    # Resolve operand entries from Vault V2 (calc tokens are rejected by the
    # engine as chained → not fetched as vault entries).
    vault_lookup = [t for t in tokens if not is_calc_token(t)]
    entries = await conversation_vault_v2.get_entries_scoped_async(scope, vault_lookup)
    try:
        import time as _time

        token, entry = await secure_calculate(
            scope=scope,
            operation=operation,
            operand_tokens=tokens,
            vault_entries=entries,
            created_at=int(_time.time()),
        )
    except CalcError as exc:
        logger.info("dlp_secure_calc_denied", scope_fp=scope.fingerprint(), code=exc.code,
                    operation=operation.value, operands=len(tokens))
        return JSONResponse(status_code=422, content={"status": "denied", "code": exc.code})
    except Exception as exc:  # never leak raw on an unexpected failure
        logger.warning("dlp_secure_calc_error", scope_fp=scope.fingerprint(),
                       exception_type=type(exc).__name__)
        return JSONResponse(status_code=500, content={"status": "denied", "code": "internal_error"})

    # Safe telemetry only — operand token refs + operation, NEVER raw values.
    logger.info(
        "dlp_secure_calc",
        scope_fp=scope.fingerprint(),
        operation=operation.value,
        operand_count=len(entry.operand_tokens),
        result_token=token,
    )
    return {"result_token": token, "status": "complete"}


@router.post("/internal/dlp/feature-scope")
async def feature_scope_effective(
    auth=Depends(get_resolved_api_key),
    x_internal_service_secret: str | None = Header(default=None),
):
    """Phase 4A.2.7 — internal-trusted diagnostics: the EFFECTIVE per-tenant
    feature state for the request's derived scope. No secrets, no raw values —
    only booleans + the scope mode + a non-reversible scope fingerprint."""
    configured = (settings.internal_service_secret or "").strip()
    supplied = (x_internal_service_secret or "").strip()
    if (
        not configured
        or not supplied
        or not hmac.compare_digest(supplied, configured)
        or not bool(getattr(auth, "internal_trusted", False))
    ):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    from app.services.dlp.feature_scope import effective_feature_enabled, scope_mode

    ctx = build_dlp_request_context(
        auth=auth, direction=ScanDirection.OUTPUT, source_type=SourceType.WEBCHAT
    )
    tid, pid = getattr(ctx, "tenant_id", None), getattr(ctx, "project_id", None)

    def eff(flag: bool) -> bool:
        return effective_feature_enabled(flag, tid, pid)

    return {
        "tenant_id": tid,
        "project_id": pid,
        "scope_mode": scope_mode(),
        "policy_v2_effective": eff(settings.dlp_policy_engine_v2_enabled),
        "vault_v2_effective": eff(settings.dlp_vault_v2_enabled),
        "canonical_artifact_effective": eff(settings.dlp_canonical_artifact_enabled),
        "secure_calc_effective": eff(settings.dlp_secure_calc_enabled),
        # canonical_history + display_lifecycle are chat-edge-side flags; the
        # gateway reports its own effective view (the same scope gate applies in
        # chat-edge). Booleans here reflect the gateway's scope decision only.
        "canonical_history_effective": eff(settings.dlp_canonical_artifact_enabled),
        "display_lifecycle_effective": eff(settings.dlp_canonical_artifact_enabled),
    }
