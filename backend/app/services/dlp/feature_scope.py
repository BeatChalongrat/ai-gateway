"""Tenant-scoped DLP feature gating — Phase 4A.2.7.

A canary-sensitive feature (Policy V2, Vault V2, Canonical Artifact, Canonical
History, Display Lifecycle, Secure Calculator) is effectively enabled for a
request ONLY when BOTH hold:

    effective = global_flag_enabled AND scope_allows(tenant_id, project_id)

``scope_allows`` is driven by ``DLP_FEATURE_SCOPE_MODE``:
  * ``off``       → never (default — safe).
  * ``allowlist`` → only (tenant_id/project_id) pairs in DLP_FEATURE_ALLOWLIST.
  * ``global``    → all managed tenants (full rollout only).

Fail-closed: an unknown mode, a missing tenant/project, or a malformed allowlist
entry yields ``False``. No raw values, secrets, or tenant ids are logged here.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings

SCOPE_OFF = "off"
SCOPE_ALLOWLIST = "allowlist"
SCOPE_GLOBAL = "global"
_VALID_MODES = frozenset({SCOPE_OFF, SCOPE_ALLOWLIST, SCOPE_GLOBAL})


@lru_cache(maxsize=8)
def _parse_allowlist(raw: str) -> frozenset[str]:
    """Parse ``tenant/project,tenant/project`` into a normalized set.

    Cached on the raw string. Malformed entries (no single ``/`` separator,
    blank tenant or project) are skipped — never a wildcard. Whitespace and
    surrounding slashes are trimmed; matching is exact thereafter.
    """
    out: set[str] = set()
    for chunk in (raw or "").split(","):
        c = chunk.strip()
        if not c or c.count("/") != 1:
            continue
        tenant, project = c.split("/", 1)
        tenant, project = tenant.strip(), project.strip()
        if tenant and project:
            out.add(f"{tenant}/{project}")
    return frozenset(out)


def scope_mode() -> str:
    mode = (getattr(settings, "dlp_feature_scope_mode", SCOPE_OFF) or SCOPE_OFF).strip().lower()
    return mode if mode in _VALID_MODES else SCOPE_OFF  # unknown → fail closed


def is_in_scope(tenant_id, project_id) -> bool:
    """Whether (tenant_id, project_id) is allowed by the current scope mode.

    Independent of any global flag. ``off`` → False; ``global`` → True (any
    valid tenant+project); ``allowlist`` → membership. Missing tenant/project →
    False.
    """
    mode = scope_mode()
    if mode == SCOPE_OFF:
        return False
    t = (tenant_id or "").strip() if isinstance(tenant_id, str) else ""
    p = (project_id or "").strip() if isinstance(project_id, str) else ""
    if not t or not p:
        return False  # missing scope → disabled
    if mode == SCOPE_GLOBAL:
        return True
    # allowlist
    return f"{t}/{p}" in _parse_allowlist(getattr(settings, "dlp_feature_allowlist", "") or "")


def effective_feature_enabled(global_flag: bool, tenant_id, project_id) -> bool:
    """The single rule: a feature is on iff its global flag is on AND the
    request's tenant/project is in scope. Fail-closed on any gap."""
    return bool(global_flag) and is_in_scope(tenant_id, project_id)


def _ids_from_context(context) -> tuple:
    if context is None:
        return (None, None)
    return (getattr(context, "tenant_id", None), getattr(context, "project_id", None))


def effective_for_context(global_flag: bool, context) -> bool:
    """Convenience for call sites that hold a DLPRequestContext / auth-like
    object exposing ``tenant_id`` + ``project_id``."""
    t, p = _ids_from_context(context)
    return effective_feature_enabled(global_flag, t, p)
