"""FIX120 — the fallback policy is data, and this module is its only reader.

``fallback_policy.json`` beside this file is a byte-identical copy of
``docs/contracts/fallback_policy.json`` (a test pins that; the docs copy is
the one to edit). Everything the gateway used to decide in three separate
code sets — which error codes walk the chain, how far a failure reaches,
which chains exist, the two ceilings, the health-marker cooldown — now
reads from that file, and the shipped values reproduce f042040 exactly.

What this module adds on top of the file, each OFF or empty by default so
a merge changes nothing:

* a **kill switch** (``system_settings.fallback_enabled``) read through a
  30 s cache that the admin route clears on write — no deploy to flip it;
* **overrides** per deployment (``fallback_policy``) and per tenant
  (``fallback_policy:tenant:<id>``), JSON rows in ``system_settings``,
  validated against the file's ``overridable`` list; ``hard_forbidden``
  rows can never be changed by an override;
* the **data boundary**: a candidate outside the request's allowed
  providers is never tried, and no setting can permit it;
* a per-model **circuit breaker** (``allowed_fails`` in ``window_s`` →
  ``cooldown_s``), which when disabled leaves the one-failure marker
  exactly as it was;
* a **cost cap** hook (price ratio vs the requested model, tenant daily
  spend), disabled by default;
* **coverage**: which models have a chain and which do not, as numbers.

Every read fails towards "as before": an unreadable settings table, a
malformed override, a Redis outage — none of them can make fallback do
more than the file says.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import structlog

log = structlog.get_logger(__name__)

_PACKAGED_PATH = Path(__file__).with_name("fallback_policy.json")

# Actions that walk a chain. Everything else is ``give_up`` or the adapter's
# own same-model retry (``retry_same``), which happens before a gateway
# error code exists.
WALK_ACTIONS: frozenset[str] = frozenset(
    {"cooldown_then_next", "next_in_chain", "context_window_chain", "content_policy_chain"}
)
GIVE_UP = "give_up"

_TTL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------

_base_lock = threading.Lock()
_base: dict | None = None


def _load_file() -> dict:
    data = json.loads(_PACKAGED_PATH.read_text(encoding="utf-8"))
    # ``proposals`` is a draft for the owner, never policy. Dropping it here
    # is what makes "declared but not enabled" a fact rather than a promise.
    data.pop("proposals", None)
    return data


def base_policy() -> dict:
    """The shipped policy (the packaged file, ``proposals`` removed).

    Returned object is shared — treat it as read-only. ``effective_policy``
    hands out copies.
    """
    global _base
    with _base_lock:
        if _base is None:
            _base = _load_file()
        return _base


def packaged_policy_path() -> Path:
    return _PACKAGED_PATH


# ---------------------------------------------------------------------------
# The decision table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    action: str
    signal: str  # http_status | error_code | terminal_cause | stop_reason | none
    value: str | None
    reason: str

    @property
    def walks(self) -> bool:
        return self.action in WALK_ACTIONS


def decide(
    *,
    error_code: str | None = None,
    terminal_cause: str | None = None,
    stop_reason: str | None = None,
    http_status: int | str | None = None,
    policy: dict | None = None,
) -> Decision:
    """One signal in, one action out, read from the table — never an ``if``
    chain in code.

    Precedence when several signals are given: a terminal cause other than
    ``error`` (the run ended for a reason that is not a failure code) →
    a stop reason (an answer was delivered) → an HTTP status (the adapter
    stage) → the error code. The two opt-in chains resolve to ``give_up``
    unless the policy's ``opt_in`` says otherwise.
    """
    pol = policy or base_policy()
    table = pol["decision_table"]

    if terminal_cause is not None and terminal_cause != "error":
        action = table["terminal_cause"].get(terminal_cause, GIVE_UP)
        return Decision(action, "terminal_cause", terminal_cause, "terminal_cause row")
    if stop_reason is not None:
        action = table["stop_reason"].get(stop_reason, GIVE_UP)
        return Decision(action, "stop_reason", stop_reason, "stop_reason row")
    if http_status is not None:
        action = table["http_status"].get(str(http_status), GIVE_UP)
        return Decision(action, "http_status", str(http_status), "http_status row")
    if error_code:
        row = table["error_code"]
        if error_code in row:
            action, reason = row[error_code], "error_code row"
        else:
            action, reason = table.get("error_code_default", GIVE_UP), "error_code default"
        opt = pol.get("opt_in") or {}
        if action == "context_window_chain" and not opt.get("context_window_fallback"):
            return Decision(GIVE_UP, "error_code", error_code, "context_window_fallback opt-in is off")
        if action == "content_policy_chain" and not opt.get("content_policy_fallback"):
            return Decision(GIVE_UP, "error_code", error_code, "content_policy_fallback opt-in is off")
        return Decision(action, "error_code", error_code, reason)
    return Decision(GIVE_UP, "none", None, "no signal")


def eligible_error_codes(policy: dict | None = None) -> frozenset[str]:
    """Codes whose decision walks a chain under ``policy`` (opt-ins applied)."""
    pol = policy or base_policy()
    return frozenset(
        code
        for code in pol["decision_table"]["error_code"]
        if decide(error_code=code, policy=pol).walks
    )


def failure_scope_for(code: str | None, status: Any, policy: dict | None = None) -> str:
    """``request`` | ``account`` | ``model`` from the file's ``failure_scope``."""
    pol = policy or base_policy()
    fs = pol["failure_scope"]
    if code and code in fs.get("request", ()):
        return "request"
    if code and code in fs.get("model", ()):
        return "model"
    if isinstance(status, int):
        by = fs.get("by_status", {})
        if str(status) in by:
            return by[str(status)]
        if status >= 500 and "5xx" in by:
            return by["5xx"]
    if code and code in fs.get("account", ()):
        return "account"
    return fs.get("default", "model")


def provider_wide_codes(policy: dict | None = None) -> frozenset[str]:
    pol = policy or base_policy()
    return frozenset(pol["health_marker"].get("provider_wide_codes", ()))


def health_cooldown_s(policy: dict | None = None) -> int:
    pol = policy or base_policy()
    return int(pol["health_marker"].get("cooldown_s", 60))


def limits(kind: str, policy: dict | None = None) -> dict:
    """``kind`` is ``non_stream`` or ``stream``."""
    pol = policy or base_policy()
    return dict(pol["limits"][kind])


# ---------------------------------------------------------------------------
# system_settings reads: the kill switch and the overrides (30 s cache)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def clear_cache() -> None:
    """Drop every memoised settings read (admin route calls this on write)."""
    with _cache_lock:
        _cache.clear()


def _read_setting(key: str) -> str | None:
    from app.core.db import SessionLocal
    from app.models.system_setting import SystemSettingORM

    with SessionLocal() as s:
        row = s.get(SystemSettingORM, key)
        return row.value if row is not None else None


def _cached(key: str, loader):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def kill_switch_key() -> str:
    return base_policy()["kill_switch"]["setting_key"]


def is_enabled() -> bool:
    """The kill switch. Absent row or any read error → the file's default."""
    default = bool(base_policy()["kill_switch"].get("default", True))

    def _load() -> bool:
        try:
            raw = _read_setting(kill_switch_key())
        except Exception as exc:  # noqa: BLE001 — a settings outage must not change behaviour
            log.warning("fallback_kill_switch_read_failed", error=str(exc)[:200])
            return default
        if raw is None:
            return default
        return raw.strip().lower() not in ("false", "0", "no", "off")

    return bool(_cached("kill_switch", _load))


def _override_setting_key(tenant_id: str | None) -> str:
    ov = base_policy()["overrides"]
    if tenant_id:
        return f"{ov['tenant_setting_key_prefix']}{tenant_id}"
    return ov["deployment_setting_key"]


def read_override(tenant_id: str | None) -> dict | None:
    """The stored override for the deployment (``None`` tenant) or a tenant.

    A row that does not parse, or does not validate, is logged and treated
    as absent — a bad override can never make fallback do more than the
    file says.
    """
    key = _override_setting_key(tenant_id)

    def _load() -> dict | None:
        try:
            raw = _read_setting(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("fallback_override_read_failed", key=key, error=str(exc)[:200])
            return None
        if not raw or not raw.strip():
            return None
        try:
            data = json.loads(raw)
            return validate_override(data, is_tenant=bool(tenant_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("fallback_override_invalid", key=key, error=str(exc)[:200])
            return None

    return _cached(f"override:{key}", _load)


# ---------------------------------------------------------------------------
# Override validation + merge
# ---------------------------------------------------------------------------

_HOP_KEYS = {"provider", "model"}


def _check_chain_map(name: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object of model → [hops]")
    for model, hops in value.items():
        if not isinstance(model, str) or not model:
            raise ValueError(f"{name}: model keys must be non-empty strings")
        if not isinstance(hops, list):
            raise ValueError(f"{name}.{model} must be a list of hops")
        for hop in hops:
            if not isinstance(hop, dict) or set(hop) != _HOP_KEYS:
                raise ValueError(f"{name}.{model}: each hop is {{provider, model}}")
            if not all(isinstance(hop[k], str) and hop[k] for k in _HOP_KEYS):
                raise ValueError(f"{name}.{model}: hop provider/model must be non-empty strings")


def _check_number(name: str, value: Any, *, allow_none: bool = False, minimum: float = 0.0) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def validate_override(data: Any, *, is_tenant: bool) -> dict:
    """Return ``data`` if it is a legal override, else raise ``ValueError``.

    Legal = an object whose keys are all in the file's ``overridable`` list
    (``allowed_providers`` only for a tenant), whose chains are well-formed,
    whose ``decision_table.error_code`` rows use actions from the closed
    set and never touch a ``hard_forbidden`` code, and whose numbers are
    numbers.
    """
    base = base_policy()
    if not isinstance(data, dict):
        raise ValueError("override must be a JSON object")
    overridable = set(base["overrides"]["overridable"])
    forbidden_codes = set(base["hard_forbidden"]["error_code"])
    actions = set(base["actions"])

    for key, value in data.items():
        if key == "chains":
            if not isinstance(value, dict):
                raise ValueError("chains must be an object")
            for sub, chains in value.items():
                if f"chains.{sub}" not in overridable:
                    raise ValueError(f"chains.{sub} is not overridable")
                _check_chain_map(f"chains.{sub}", chains)
        elif key == "decision_table":
            if not isinstance(value, dict) or set(value) - {"error_code"}:
                raise ValueError("decision_table override may only carry error_code")
            if "decision_table.error_code" not in overridable:
                raise ValueError("decision_table.error_code is not overridable")
            rows = value.get("error_code", {})
            if not isinstance(rows, dict):
                raise ValueError("decision_table.error_code must be an object")
            for code, action in rows.items():
                if code in forbidden_codes:
                    raise ValueError(f"decision_table.error_code.{code} is hard-forbidden and cannot be overridden")
                if action not in actions:
                    raise ValueError(f"decision_table.error_code.{code}: unknown action '{action}'")
        elif key == "allowed_providers":
            if not is_tenant:
                raise ValueError("allowed_providers is a tenant-level boundary; set it on a tenant override")
            if key not in overridable:
                raise ValueError("allowed_providers is not overridable")
            if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
                raise ValueError("allowed_providers must be a list of provider names")
        elif key == "limits":
            if key not in overridable or not isinstance(value, dict):
                raise ValueError("limits must be an object")
            for kind, lim in value.items():
                if kind not in ("non_stream", "stream") or not isinstance(lim, dict):
                    raise ValueError("limits keys are non_stream / stream")
                for k, v in lim.items():
                    if k == "max_candidates":
                        _check_number(f"limits.{kind}.{k}", v)
                        if isinstance(v, float) and not v.is_integer():
                            raise ValueError(f"limits.{kind}.{k} must be an integer")
                    elif k in ("attempt_timeout_s", "min_attempt_runway_s"):
                        _check_number(f"limits.{kind}.{k}", v)
                    elif k == "total_budget_s":
                        _check_number(f"limits.{kind}.{k}", v, allow_none=True)
                    else:
                        raise ValueError(f"limits.{kind}.{k} is not a known limit")
        elif key == "breaker":
            if key not in overridable or not isinstance(value, dict):
                raise ValueError("breaker must be an object")
            for k, v in value.items():
                if k == "enabled":
                    if not isinstance(v, bool):
                        raise ValueError("breaker.enabled must be a boolean")
                elif k in ("allowed_fails", "window_s", "cooldown_s"):
                    _check_number(f"breaker.{k}", v, minimum=1)
                else:
                    raise ValueError(f"breaker.{k} is not a known field")
        elif key == "cost_cap":
            if key not in overridable or not isinstance(value, dict):
                raise ValueError("cost_cap must be an object")
            for k, v in value.items():
                if k == "enabled":
                    if not isinstance(v, bool):
                        raise ValueError("cost_cap.enabled must be a boolean")
                elif k in ("max_cost_ratio_vs_requested", "tenant_daily_usd"):
                    _check_number(f"cost_cap.{k}", v, allow_none=True)
                else:
                    raise ValueError(f"cost_cap.{k} is not a known field")
        elif key == "opt_in":
            if key not in overridable or not isinstance(value, dict):
                raise ValueError("opt_in must be an object")
            for k, v in value.items():
                if k not in ("context_window_fallback", "content_policy_fallback") or not isinstance(v, bool):
                    raise ValueError(f"opt_in.{k} must be a boolean flag named in the file")
        else:
            raise ValueError(f"'{key}' is not overridable")
    return data


def _merge(into: dict, override: dict) -> None:
    """Deep-merge ``override`` into ``into``: dicts merge, everything else
    is replaced. Chain lists replace whole (a hop list is one decision)."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict) and key != "allowed_providers":
            _merge(into[key], value)
        else:
            into[key] = copy.deepcopy(value)


def apply_overrides(base: dict, *layers: dict | None) -> dict:
    """Pure: base ← deployment ← tenant. Chain maps merge per model."""
    eff = copy.deepcopy(base)
    for layer in layers:
        if not layer:
            continue
        for key, value in layer.items():
            if key == "chains":
                eff.setdefault("chains", {})
                for sub, chains in value.items():
                    eff["chains"].setdefault(sub, {})
                    for model, hops in chains.items():
                        eff["chains"][sub][model] = copy.deepcopy(hops)
            elif key == "decision_table":
                eff["decision_table"]["error_code"].update(value.get("error_code", {}))
            elif key == "allowed_providers":
                eff["allowed_providers"] = list(value)
            else:
                if isinstance(value, dict) and isinstance(eff.get(key), dict):
                    _merge(eff[key], value)
                else:
                    eff[key] = copy.deepcopy(value)
    return eff


def effective_policy(tenant_id: str | None) -> dict:
    """file ← deployment override ← tenant override.

    With no override stored anywhere this IS the shipped policy object
    (identity, not a copy) — the callers use that to take the pre-FIX120
    code path byte for byte; with an override it is a fresh merged copy."""
    dep = read_override(None)
    ten = read_override(tenant_id) if tenant_id else None
    if not dep and not ten:
        return base_policy()
    return apply_overrides(base_policy(), dep, ten)


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


def chain_for(model: str, original_provider: str, *, policy: dict | None = None) -> list[dict[str, str]]:
    """The ordinary chain (curated → legacy tier), from ``policy``'s chains.

    An entry an OVERRIDE put under ``chains.curated`` (absent from the
    shipped file, or different from it) always wins — the legacy rule that
    a tier-owned model ignores the curated table would otherwise make an
    operator's override for ``gpt-4o`` a silent no-op.
    """
    from app.services.providers.model_tiers import _alias, chain_from_tables

    pol = policy or base_policy()
    if pol is not base_policy():
        alias = _alias(model)
        eff_hops = pol["chains"].get("curated", {}).get(alias)
        base_hops = base_policy()["chains"].get("curated", {}).get(alias)
        if eff_hops is not None and eff_hops != base_hops:
            out = [
                dict(h) for h in eff_hops
                if not (h["provider"] == original_provider and _alias(h["model"]) == alias)
            ]
            if out:
                return out
    return chain_from_tables(
        model,
        original_provider,
        curated=pol["chains"].get("curated", {}),
        tiers=pol["chains"].get("tiers", {}),
    )


def chain_for_action(
    action: str, model: str, original_provider: str, *, policy: dict | None = None
) -> list[dict[str, str]]:
    """The chain an action walks: the ordinary chain, or one of the two
    opt-in lists (keyed by the bare alias like the curated table)."""
    pol = policy or base_policy()
    if action in ("cooldown_then_next", "next_in_chain"):
        return chain_for(model, original_provider, policy=pol)
    if action in ("context_window_chain", "content_policy_chain"):
        from app.services.providers.model_tiers import _alias

        sub = "context_window" if action == "context_window_chain" else "content_policy"
        table = pol["chains"].get(sub, {})
        hops = table.get(model) or table.get(_alias(model)) or []
        return [dict(h) for h in hops if not (h["provider"] == original_provider and h["model"] == model)]
    return []


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------


def allowed_set(policy: dict | None, request_allowed: Iterable[str] | None) -> frozenset[str] | None:
    """The providers a fallback may reach: the request's allowed set (the
    project's, via metadata) intersected with the tenant override's, when
    either exists. ``None`` = no boundary (today's behaviour)."""
    sets: list[frozenset[str]] = []
    if request_allowed:
        sets.append(frozenset(str(p) for p in request_allowed))
    tenant = (policy or {}).get("allowed_providers")
    if tenant:
        sets.append(frozenset(str(p) for p in tenant))
    if not sets:
        return None
    out = sets[0]
    for s in sets[1:]:
        out = out & s
    return out


def outside_boundary(provider: str, allowed: frozenset[str] | None) -> bool:
    return allowed is not None and provider not in allowed


# ---------------------------------------------------------------------------
# Cost cap hook
# ---------------------------------------------------------------------------


def cost_blocks_candidate(policy: dict, requested_model: str, candidate_model: str) -> str | None:
    """Reason a candidate is refused by the price-ratio rule, or ``None``.

    Off unless ``cost_cap.enabled`` and ``max_cost_ratio_vs_requested`` are
    set. Prices come from the model registry; a model with no known price
    is never blocked (nothing to compare)."""
    cap = policy.get("cost_cap") or {}
    if not cap.get("enabled"):
        return None
    ratio = cap.get("max_cost_ratio_vs_requested")
    if ratio is None:
        return None
    from app.services.providers.model_registry import get_candidate

    req, cand = get_candidate(requested_model), get_candidate(candidate_model)
    if req is None or cand is None:
        return None
    req_price = float(req.cost_per_input_token or 0.0)
    cand_price = float(cand.cost_per_input_token or 0.0)
    if cand_price > req_price * float(ratio):
        return f"cost_ratio {cand_price:.3g} > {req_price:.3g} x {ratio}"
    return None


def tenant_daily_spend_usd(session, tenant_id: str) -> float:
    """Estimated spend over the last 24 h from ``request_logs``."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.models.request_log import RequestLogORM

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    total = session.execute(
        select(func.coalesce(func.sum(RequestLogORM.estimated_cost_usd), 0.0)).where(
            RequestLogORM.tenant_id == tenant_id, RequestLogORM.created_at >= since
        )
    ).scalar()
    return float(total or 0.0)


def cost_blocks_tenant(policy: dict, tenant_id: str | None, spend_reader=None) -> str | None:
    """Reason the whole fallback walk is refused for a tenant, or ``None``.

    ``spend_reader(tenant_id) -> float`` is injected (the callers hand in a
    session-bound reader); any failure to read spend fails open."""
    cap = policy.get("cost_cap") or {}
    if not cap.get("enabled") or not tenant_id:
        return None
    ceiling = cap.get("tenant_daily_usd")
    if ceiling is None or spend_reader is None:
        return None
    try:
        spent = float(spend_reader(tenant_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("fallback_cost_cap_spend_read_failed", tenant_id=tenant_id, error=str(exc)[:200])
        return None
    if spent >= float(ceiling):
        return f"tenant_daily_usd {spent:.4f} >= {ceiling}"
    return None


# ---------------------------------------------------------------------------
# Circuit breaker (per provider, model)
# ---------------------------------------------------------------------------

_BREAKER_KEY_PREFIX = "provider:fails:"


async def breaker_allows_marking(provider: str, model: str | None, policy: dict | None = None) -> tuple[bool, int]:
    """Whether this failure should set the health marker, and the marker TTL.

    Breaker off (the default): ``(True, health_marker.cooldown_s)`` — every
    eligible failure marks at once, as before. Breaker on: count failures
    per pair in a window; mark only when the count reaches
    ``allowed_fails``, with ``cooldown_s`` as the TTL. Redis trouble fails
    towards the legacy answer."""
    pol = policy or base_policy()
    br = pol.get("breaker") or {}
    legacy_ttl = health_cooldown_s(pol)
    if not br.get("enabled"):
        return True, legacy_ttl
    allowed = int(br.get("allowed_fails", 3))
    window = int(br.get("window_s", 60))
    cooldown = int(br.get("cooldown_s", legacy_ttl))
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        key = f"{_BREAKER_KEY_PREFIX}{provider}:{model or '*'}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window)
        return count >= allowed, cooldown
    except Exception as exc:  # noqa: BLE001
        log.warning("fallback_breaker_redis_failed", error=str(exc)[:200])
        return True, legacy_ttl


# ---------------------------------------------------------------------------
# Coverage — which models have a chain
# ---------------------------------------------------------------------------


def coverage(candidates=None, *, policy: dict | None = None, chat_only: bool = True) -> dict:
    """Per-model chain report + counts.

    ``candidates`` defaults to the registry's active models (``show_in_chat``
    ones when ``chat_only``). Source names where the chain came from:
    ``override`` (a curated entry the effective policy added or replaced),
    ``curated``, ``tier`` or ``none``."""
    from app.services.providers.model_registry import get_all_candidates
    from app.services.providers.model_tiers import _alias, get_tier

    pol = policy or base_policy()
    base_curated = base_policy()["chains"].get("curated", {})
    eff_curated = pol["chains"].get("curated", {})
    cands = list(candidates) if candidates is not None else list(get_all_candidates())
    if chat_only:
        cands = [c for c in cands if getattr(c, "show_in_chat", True)]
    rows: list[dict] = []
    for c in cands:
        chain = chain_for(c.model, c.provider, policy=pol)
        alias = _alias(c.model)
        if not chain:
            source = "none"
        elif alias in eff_curated and eff_curated.get(alias) != base_curated.get(alias):
            source = "override"
        elif get_tier(c.model) is None and alias in eff_curated:
            source = "curated"
        else:
            source = "tier"
        rows.append(
            {
                "provider": c.provider,
                "model": c.model,
                "has_chain": bool(chain),
                "chain": chain,
                "source": source,
            }
        )
    rows.sort(key=lambda r: (r["has_chain"], r["provider"], r["model"]))
    with_chain = sum(1 for r in rows if r["has_chain"])
    return {
        "summary": {"total": len(rows), "with_chain": with_chain, "without_chain": len(rows) - with_chain},
        "models": rows,
    }


def _main(argv: list[str]) -> int:  # pragma: no cover — operator command
    """``python -m app.services.gateway.fallback_policy --coverage``"""
    if "--coverage" not in argv:
        print("usage: python -m app.services.gateway.fallback_policy --coverage [--all]")
        return 2
    loaded_from = "seed list (no database reachable)"
    try:
        import asyncio

        from app.core.db import SessionLocal
        from app.services.providers.model_registry import _registry

        with SessionLocal() as s:
            asyncio.run(_registry.reload(s))
        loaded_from = "model_configs (database)"
    except Exception as exc:  # noqa: BLE001
        loaded_from += f": {type(exc).__name__}"
    report = coverage(chat_only="--all" not in argv)
    s = report["summary"]
    print(f"models from {loaded_from}")
    print(f"total={s['total']} with_chain={s['with_chain']} without_chain={s['without_chain']}")
    for r in report["models"]:
        hops = " -> ".join(f"{h['provider']}:{h['model']}" for h in r["chain"]) or "-"
        print(f"{'chain' if r['has_chain'] else 'NONE ':5} {r['provider']:12} {r['model']:44} {hops}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
