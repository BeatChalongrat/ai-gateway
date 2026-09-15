"""Cross-provider fallback for failed primary dispatches.

When ``ProviderDispatcher.dispatch`` fails with a fallback-eligible
``GatewayError`` (timeout / 5xx / 429 / 404 / unavailable), the gateway
orchestrator hands control to :class:`FallbackOrchestrator`. The
fallback runner:

1. Looks up the model's chain (``fallback_policy.chain_for_action``: the
   curated table, then the legacy tier table — both data in
   ``docs/contracts/fallback_policy.json``, plus any deployment / tenant
   override).
2. Walks at most ``max_candidates`` candidates (default 2 → 3 total
   attempts including the original).
3. For each candidate, resolves an API key via
   :class:`ProviderKeyService`. Missing keys are recorded as a
   no-op attempt and skipped — they are not counted toward the
   per-attempt budget so a misconfigured tenant still gets the
   benefit of two real fallback tries.
4. Dispatches with a 10s timeout. Success returns immediately
   wrapped in :class:`FallbackResult`. Failure records a
   :class:`FallbackAttempt` and continues.
5. If every candidate fails, the *last* GatewayError is re-raised so
   the original orchestrator's error path still maps it to a 502 /
   504 / 429 response.

FIX120 — every rule this module used to hold as a Python set now reads
from the policy file through :mod:`app.services.gateway.fallback_policy`:
which codes walk (``decide``), how far a failure reaches
(``failure_scope_for``), which codes mark a whole provider, the cooldown,
the two ceilings. The module-level names (``FALLBACK_ELIGIBLE_ERRORS``,
``_PROVIDER_WIDE_CODES``, ``FALLBACK_TOTAL_BUDGET_S`` …) are kept as
read-only views of the shipped file. New, each off or empty by default:
the kill switch, the data boundary, the cost cap, the circuit breaker, a
``fallback_attempt`` log line per candidate carrying the turn id, and the
Prometheus counters.

The orchestrator is intentionally narrow: it does not run DLP, does
not touch the request_log queue, and does not build a public response
shape. Those concerns stay in :mod:`app.services.orchestrator`, which
wraps the returned ``ProviderResponse`` exactly like the success path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from collections.abc import Iterable

import structlog

from app.core.exceptions import GatewayError, ProviderError
from app.domain.entities import RouteDecision
from app.services.gateway import fallback_policy as policy_mod
from app.services.providers.model_tiers import get_fallback_chain
from app.services.gateway.fallback_policy import (
    WALK_ACTIONS,
    allowed_set,
    breaker_allows_marking,
    chain_for_action,
    cost_blocks_candidate,
    cost_blocks_tenant,
    decide,
    outside_boundary,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.domain.entities import RequestContext
    from app.domain.providers import ProviderResponse
    from app.schemas.common import Message
    from app.services.capability_detection import RequiredCapabilities
    from app.schemas.public import ChatCompletionsRequest
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.services.provider_key_service import ProviderKeyService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Redis-backed provider health markers.
#
# The streaming branch in ``public_chat.py`` cannot run the same
# retry-and-fall-back loop the non-streaming orchestrator uses — once
# the first SSE chunk is on the wire, headers and content type are
# already committed and a mid-stream provider swap would corrupt the
# response. Instead we use a short-TTL Redis key as a *health hint*:
#
#   * Every fallback-eligible failure SETs ``provider:health:{name}``
#     with a 60-second TTL (``health_marker.cooldown_s`` in the policy).
#   * Every success DELs the same key.
#
# Before a streaming request invokes ``adapter.stream_chat()`` the
# route's primary provider is checked against this marker; if the
# key is present, the streaming branch swaps to the first viable
# fallback candidate BEFORE any bytes are written. Stale markers
# expire on their own.
#
# Failures here are silently ignored — the gateway must keep serving
# traffic during a Redis outage, and the worst case is a missed
# health hint (which is recovered by the in-generator try/except
# anyway).
# ---------------------------------------------------------------------------

_HEALTH_KEY_PREFIX = "provider:health:"
_HEALTH_TTL_SECONDS = policy_mod.health_cooldown_s()

# FIX49 Part C — marker scope. Markers are per (provider, model). Only
# error codes that mean "the provider's infrastructure itself is down"
# still set the provider-wide key — a timeout or connection failure says
# nothing about one model, it says the box is unreachable.
# FIX51 Part 4 (P1-F) — ``provider_no_credit`` joins the set.
# FIX120 — the set is ``health_marker.provider_wide_codes`` in the policy.
_PROVIDER_WIDE_CODES = policy_mod.provider_wide_codes()


# ---------------------------------------------------------------------------
# FIX51 Part 4 (P1-F) — failure SCOPE decides how deep the chain walks,
# not a fixed attempt count alone.
#
#   * "model"   — only this model is broken (404 / pulled id / can't do
#                 tools): keep walking, siblings may answer.
#   * "account" — the whole account is broken (no credits, rejected
#                 credentials, 5xx, rate limit, outage): never try
#                 another candidate on the SAME provider; cross to a
#                 different account.
#   * "request" — the request itself is doomed (too long, rejected as
#                 invalid, policy-blocked): stop immediately, every
#                 provider will refuse it the same way.
#
# Unknown shapes default to "model" (keep walking) so a new error code
# degrades to the old behavior, never to a dead stop.
# FIX120 — the lists are ``failure_scope`` in the policy file.
# ---------------------------------------------------------------------------

_REQUEST_SCOPED_CODES = frozenset(policy_mod.base_policy()["failure_scope"]["request"])
_ACCOUNT_SCOPED_CODES = frozenset(policy_mod.base_policy()["failure_scope"]["account"])


def classify_failure_scope(exc: Exception, policy: dict | None = None) -> str:
    """``"model"`` | ``"account"`` | ``"request"`` for a dispatch failure.

    Codes carry most of the signal; for the generic ``provider_error``
    the mapped HTTP status in ``details`` breaks the tie (401/402/403 =
    the account's credentials or balance; >=500 = the account's infra).
    """
    code = getattr(exc, "code", None)
    details = getattr(exc, "details", None) or {}
    return policy_mod.failure_scope_for(code, details.get("status"), policy)


def _health_key(provider: str, model: str | None = None) -> str:
    if model:
        return f"{_HEALTH_KEY_PREFIX}{provider}:{model}"
    return f"{_HEALTH_KEY_PREFIX}{provider}"


async def mark_provider_unhealthy(
    provider: str,
    model: str | None = None,
    *,
    error_code: str | None = None,
    policy: dict | None = None,
) -> None:
    """Set the health marker with the policy's cooldown TTL.

    With ``model`` the marker is scoped to that (provider, model) pair;
    the provider-wide key is ALSO set when ``error_code`` is an
    infrastructure-class failure (``_PROVIDER_WIDE_CODES``). Without
    ``model`` (legacy callers) the provider-wide key is set as before.
    Idempotent — repeated calls refresh the TTL.

    FIX120 — with the circuit breaker enabled (off by default) the marker
    is set only once ``allowed_fails`` failures landed inside the window.
    """
    if not provider:
        return
    try:
        should_mark, ttl = await breaker_allows_marking(provider, model, policy)
        if not should_mark:
            logger.info(
                "fallback_breaker_below_threshold",
                provider=provider,
                model=model,
                error_code=error_code,
            )
            return
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        await client.set(_health_key(provider, model), "unhealthy", ex=ttl)
        wide = policy_mod.provider_wide_codes(policy) if policy else _PROVIDER_WIDE_CODES
        if model and error_code in wide:
            await client.set(_health_key(provider), "unhealthy", ex=ttl)
    except Exception:  # noqa: BLE001
        # Health hint is best-effort — Redis outage must not break
        # the request path.
        pass


async def clear_provider_health(provider: str, model: str | None = None) -> None:
    """Delete the health marker(s) for ``provider`` (and ``model``).

    Called after every successful dispatch so a recovered provider
    doesn't keep getting skipped for the rest of the TTL window. A
    success on any model also clears the provider-wide key — the
    infrastructure evidently answers.
    """
    if not provider:
        return
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        if model:
            await client.delete(_health_key(provider, model))
        await client.delete(_health_key(provider))
    except Exception:  # noqa: BLE001
        pass


async def unhealthy_pairs(
    pairs: Iterable[tuple[str, str]],
) -> set[tuple[str, str]]:
    """The subset of ``(provider, model)`` pairs with a live pair marker.

    FIX50 P1-A (OPEN_DECISIONS #58): the auto-router's pool filter only
    saw provider-wide markers, so a model whose account just ran dry
    stayed in ``auto``'s pool until the pre-check swapped it a beat
    later. One pipelined EXISTS round-trip checks every candidate pair
    at once — never one GET per model on the hot path.

    Provider-wide markers are NOT checked here; the caller already
    prunes whole providers via ``is_provider_unhealthy``. Fail-open:
    any Redis error returns the empty set.
    """
    unique = [(p, m) for (p, m) in dict.fromkeys(pairs) if p and m]
    if not unique:
        return set()
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        pipe = client.pipeline(transaction=False)
        for provider, model in unique:
            pipe.exists(_health_key(provider, model))
        flags = await pipe.execute()
        return {pair for pair, flag in zip(unique, flags) if flag}
    except Exception:  # noqa: BLE001
        return set()


async def is_provider_unhealthy(provider: str, model: str | None = None) -> bool:
    """True if the (provider, model) pair — or the provider itself — is marked.

    Fail-open: any Redis error is treated as "healthy" so a Redis
    outage degrades to no-pre-check, not request-blocking.
    """
    if not provider:
        return False
    try:
        from app.services._redis_pool import get_async_redis

        client = get_async_redis()
        if model:
            if await client.exists(_health_key(provider, model)):
                return True
        exists = await client.exists(_health_key(provider))
        return bool(exists)
    except Exception:  # noqa: BLE001
        return False


# Per-attempt timeout. The primary dispatch has its own ~30s upstream
# timeout; the fallback shrinks the budget so a slow second vendor
# doesn't extend the user's wait past the original SLA.
# FIX120 — the values are ``limits.non_stream`` in the policy file.
_NS_LIMITS = policy_mod.limits("non_stream")
FALLBACK_ATTEMPT_TIMEOUT_S = float(_NS_LIMITS["attempt_timeout_s"])

# Maximum number of fallback candidates to try after the primary.
# Spec calls for 2 (so up to 3 total attempts including the original).
MAX_FALLBACK_CANDIDATES = int(_NS_LIMITS["max_candidates"])

# FIX51 Part 4 (P1-F) — the ceiling users feel is TIME, not attempt
# count. Total turn budget (primary + all fallback attempts) before the
# runner stops opening NEW attempts; each attempt's timeout also shrinks
# to what's left. A primary that burns its full ~30s leaves no budget —
# the user gets the error at ~30s instead of ~50s. The budget never
# aborts an attempt already in flight beyond its own shrunken timeout,
# so a slow-but-succeeding rescue is not killed retroactively.
FALLBACK_TOTAL_BUDGET_S = float(_NS_LIMITS["total_budget_s"])

# Don't bother opening an attempt with less than this much runway — a
# real completion can't finish, and the doomed try only delays the
# error the user is already owed.
_MIN_ATTEMPT_RUNWAY_S = float(_NS_LIMITS["min_attempt_runway_s"])


@dataclass(frozen=True)
class FallbackAttempt:
    """Record of a single fallback try that did NOT succeed.

    Persisted as one element of ``request_logs.fallback_attempts``
    (JSON-encoded). Kept flat + primitive-typed so the JSON round-trip
    is lossless and the admin dashboard can render without further
    transformation.
    """

    provider: str
    model: str
    error_code: str
    error_message: str
    latency_ms: float
    attempted_at: datetime

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "latency_ms": round(self.latency_ms, 2),
            "attempted_at": self.attempted_at.isoformat(),
        }


@dataclass
class FallbackResult:
    """Outcome of a successful fallback run.

    ``response`` is the canonical :class:`ProviderResponse` from the
    successful candidate; the caller (gateway orchestrator) wraps it
    into a ``ChatCompletionsResponse`` exactly like the success path.
    ``attempts`` enumerates every prior failed attempt; an empty list
    means the *first* fallback succeeded (attempt_number = 2).
    """

    response: "ProviderResponse"
    provider_used: str
    model_used: str
    attempt_number: int
    attempts: list[FallbackAttempt] = field(default_factory=list)


def merge_auto_candidates(
    auto_models: list,
    chain: list[dict[str, str]],
    *,
    original_provider: str,
    original_model: str,
    resolve,
) -> list[dict[str, str]]:
    """FIX35 Part C step 4 — turn the Auto router's ordered model list into
    the fallback chain, ahead of the static tier chain.

    The primary just FAILED on ``original_provider``; most failures are
    provider-level (no credits, rate limit, outage), so candidates on a
    DIFFERENT provider go first and same-provider ones after — otherwise
    the ``MAX_FALLBACK_CANDIDATES`` budget is spent re-hitting the provider
    that is down (seen live: gpt-5.6-terra → gpt-4o → gpt-4o-mini, all
    openai, all 429, while a working OpenRouter route sat third). Pure;
    ``resolve`` maps a model id to its provider (model_registry).
    """
    cross: list[dict[str, str]] = []
    same: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in auto_models or []:
        m = str(m)
        p = resolve(m) or ""
        if not p or m == original_model or (p, m) in seen:
            continue
        seen.add((p, m))
        (same if p == original_provider else cross).append({"provider": p, "model": m})
    merged = cross + same
    for e in chain or []:
        k = (e["provider"], e["model"])
        if k in seen:
            continue
        seen.add(k)
        merged.append(e)
    return merged


# ---------------------------------------------------------------------------
# FIX120 — one line per candidate the walk touches, one line per turn's
# outcome, both carrying the turn id (FIX119) so ``grep <run_id>`` shows
# every attempt. Prometheus counters ride the same two events.
# ---------------------------------------------------------------------------


def _metrics():
    from app.observability import metrics as m

    return m


def log_attempt(
    log,
    *,
    path: str,
    requested_model: str,
    attempt: int,
    provider: str,
    model: str,
    outcome: str,
    error_code: str | None = None,
    reason: str | None = None,
    latency_ms: float | None = None,
    elapsed_ms: float | None = None,
) -> None:
    """``fallback_attempt`` — the per-candidate record. ``outcome`` ∈
    success | failed | timeout | skipped. ``attempt`` counts the primary
    as 1. ``path`` ∈ non_stream | stream | layer1."""
    log.info(
        "fallback_attempt",
        path=path,
        requested_model=requested_model,
        attempt=attempt,
        candidate_provider=provider,
        candidate_model=model,
        outcome=outcome,
        error_code=error_code,
        reason=reason,
        latency_ms=int(latency_ms) if latency_ms is not None else None,
        elapsed_ms=int(elapsed_ms) if elapsed_ms is not None else None,
    )
    try:
        _metrics().FALLBACK_ATTEMPTS_TOTAL.labels(
            requested_model=requested_model, candidate_model=model, outcome=outcome, path=path
        ).inc()
    except Exception:  # noqa: BLE001 — metrics never break a turn
        pass


def log_outcome(
    log,
    *,
    path: str,
    requested_model: str,
    outcome: str,
    attempts: int,
    time_spent_s: float | None,
    served_model: str | None = None,
    served_provider: str | None = None,
    reason: str | None = None,
) -> None:
    """``fallback_outcome`` — the per-turn record. ``outcome`` ∈ rescued |
    exhausted | no_chain | disabled | forbidden | budget_exhausted |
    cost_capped."""
    log.info(
        "fallback_outcome",
        path=path,
        requested_model=requested_model,
        outcome=outcome,
        attempts=attempts,
        time_spent_ms=int(time_spent_s * 1000) if time_spent_s is not None else None,
        served_provider=served_provider,
        served_model=served_model,
        reason=reason,
    )
    try:
        m = _metrics()
        m.FALLBACK_TURNS_TOTAL.labels(requested_model=requested_model, outcome=outcome, path=path).inc()
        if time_spent_s is not None:
            m.FALLBACK_TIME_SECONDS.labels(outcome=outcome, path=path).observe(max(0.0, time_spent_s))
    except Exception:  # noqa: BLE001
        pass


def record_decision(decision) -> None:
    try:
        _metrics().FALLBACK_DECISIONS_TOTAL.labels(
            action=decision.action, error_code=decision.value or "-"
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def _spend_reader(session):
    """A ``tenant_id -> usd`` reader bound to ``session`` (sync or async).

    The non-streaming path holds an AsyncSession; the daily-spend query is
    a short aggregate, so it runs on a fresh sync session either way."""

    def _read(tenant_id: str) -> float:
        from app.core.db import SessionLocal

        with SessionLocal() as s:
            return policy_mod.tenant_daily_spend_usd(s, tenant_id)

    return _read


class FallbackOrchestrator:
    """Cross-provider fallback runner. Stateless, safe to share."""

    def __init__(
        self,
        dispatcher: "ProviderDispatcher",
        key_service: "ProviderKeyService",
        *,
        max_candidates: int | None = None,
        attempt_timeout_s: float | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.key_service = key_service
        # FIX120 — an explicit constructor value wins (tests, callers that
        # know better); otherwise the effective policy's limits apply per
        # run so a tenant override can change them without a restart.
        self._explicit_max = max_candidates
        self._explicit_timeout = attempt_timeout_s
        self.max_candidates = MAX_FALLBACK_CANDIDATES if max_candidates is None else max_candidates
        self.attempt_timeout_s = (
            FALLBACK_ATTEMPT_TIMEOUT_S if attempt_timeout_s is None else attempt_timeout_s
        )

    def _limits(self, policy: dict, kind: str) -> dict:
        lim = policy_mod.limits(kind, policy)
        if self._explicit_max is not None:
            lim["max_candidates"] = self._explicit_max
        if self._explicit_timeout is not None and kind == "non_stream":
            lim["attempt_timeout_s"] = self._explicit_timeout
        return lim

    async def run(
        self,
        *,
        original_provider: str,
        original_model: str,
        request: "ChatCompletionsRequest",
        messages: list["Message"],
        session: "Session | None",
        ctx: "RequestContext",
        original_error: Exception | None = None,
        started_at: float | None = None,
    ) -> FallbackResult:
        """Run the fallback chain. Raises the last error if every try fails.

        ``messages`` is the DLP-masked sequence the primary dispatch
        used — fallback must NOT re-scan or re-mask the request, that
        already happened upstream. The same masked payload is sent to
        every candidate so the per-vendor responses share the same
        token-space for the gateway's outbound DLP pass.
        """
        log = logger.bind(
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            request_id=ctx.request_id,
            # FIX119/FIX120 — the turn id on every attempt line.
            turn_id=getattr(ctx, "turn_id", None),
            original_provider=original_provider,
            original_model=original_model,
        )
        t_walk = time.perf_counter()
        path = "non_stream"

        # FIX120 — the kill switch. Off = the original error, untouched.
        if not policy_mod.is_enabled():
            log.warning("fallback_disabled", reason="kill_switch")
            log_outcome(log, path=path, requested_model=original_model, outcome="disabled",
                        attempts=0, time_spent_s=0.0, reason="kill_switch")
            if original_error is not None:
                raise original_error
            raise ProviderError(
                "Fallback is switched off for this deployment.",
                details={"original_provider": original_provider, "original_model": original_model},
            )

        policy = policy_mod.effective_policy(ctx.tenant_id)
        lim = self._limits(policy, "non_stream")
        max_candidates = int(lim["max_candidates"])
        attempt_timeout_s = float(lim["attempt_timeout_s"])
        total_budget_s = lim.get("total_budget_s")
        min_runway_s = float(lim.get("min_attempt_runway_s", _MIN_ATTEMPT_RUNWAY_S))

        # The decision table names the action; only a walking action gets
        # a chain. A caller that hands in no error walks the ordinary chain
        # (pre-FIX120 contract).
        action = "cooldown_then_next"
        if original_error is not None:
            decision = decide(error_code=getattr(original_error, "code", None), policy=policy)
            record_decision(decision)
            if not decision.walks:
                log.info("fallback_forbidden", action=decision.action, reason=decision.reason,
                         error_code=decision.value)
                log_outcome(log, path=path, requested_model=original_model, outcome="forbidden",
                            attempts=0, time_spent_s=0.0, reason=decision.reason)
                raise original_error
            action = decision.action

        # The shipped tables go through ``get_fallback_chain`` (a module name
        # tests patch); a policy with overrides walks its own tables.
        if policy is policy_mod.base_policy() and action in ("cooldown_then_next", "next_in_chain"):
            chain = get_fallback_chain(original_model, original_provider)
        else:
            chain = chain_for_action(action, original_model, original_provider, policy=policy)
        # FIX35 Part C step 4 — an auto-routed request carries the router's
        # own ORDERED list (already key-valid, tier-capped, task-ranked).
        # Those come first; the static cross-provider tier chain follows.
        # An explicit request never has this metadata, so its behaviour is
        # unchanged.
        _meta = getattr(request, "metadata", None)
        if not isinstance(_meta, dict):
            _meta = {}
        _auto = _meta.get("auto_candidates") or []
        if _auto and action in ("cooldown_then_next", "next_in_chain"):
            from app.services.providers.model_registry import resolve_provider

            merged = merge_auto_candidates(
                _auto, chain, original_provider=original_provider,
                original_model=original_model, resolve=resolve_provider,
            )
            if merged:
                log.info("fallback_auto_candidates", candidates=[f"{e['provider']}:{e['model']}" for e in merged[:5]])
                chain = merged
        if not chain:
            log.info("fallback_no_chain")
            log_outcome(log, path=path, requested_model=original_model, outcome="no_chain",
                        attempts=0, time_spent_s=0.0)
            # FIX98 — when there is no chain we never attempted a fallback, so
            # the ORIGINAL error is what happened and the only thing worth
            # telling the caller. Replacing it announces our routing
            # configuration instead of the event.
            if original_error is not None:
                raise original_error
            raise ProviderError(
                f"No fallback chain registered for model '{original_model}'.",
                details={
                    "original_provider": original_provider,
                    "original_model": original_model,
                },
            )

        # FIX120 — the data boundary and the tenant-level cost cap gate the
        # whole walk before a single candidate is looked at.
        allowed = allowed_set(policy, _meta.get("allowed_providers"))
        cost_reason = cost_blocks_tenant(policy, ctx.tenant_id, _spend_reader(session))
        if cost_reason:
            log.warning("fallback_cost_capped", reason=cost_reason)
            log_outcome(log, path=path, requested_model=original_model, outcome="cost_capped",
                        attempts=0, time_spent_s=0.0, reason=cost_reason)
            if original_error is not None:
                raise original_error
            raise ProviderError("Fallback refused by the tenant's cost cap.", details={"reason": cost_reason})

        from app.services.capability_detection import (
            detect_required_capabilities,
            model_meets,
        )

        # Hard capabilities (vision/tools) the request needs. A fallback
        # that can't serve them would silently produce a wrong answer.
        required_caps = detect_required_capabilities(request)

        attempts: list[FallbackAttempt] = []
        last_error: GatewayError | None = None
        tried = 0
        touched = 0  # every candidate the walk looked at (for the attempt index)

        # FIX51 Part 4 (P1-F) — failure scope + time budget.
        #
        # ``dead_accounts``: providers whose failure THIS request was
        # account-level (no credits / bad credentials / infra). Walking
        # their other models is a guaranteed repeat, so they're out for
        # the rest of this chain — the surviving attempts land on
        # different accounts by construction.
        #
        # ``deadline``: the user's wait is capped in seconds, not
        # attempts. ``started_at`` is the turn's dispatch start (the
        # primary's ~30s burn counts against it); without it the budget
        # covers the fallback walk alone.
        dead_accounts: set[str] = set()
        if original_error is not None and (
            classify_failure_scope(original_error, policy) == "account"
        ):
            dead_accounts.add(original_provider)
        t_origin = started_at if started_at is not None else time.perf_counter()
        deadline = (t_origin + float(total_budget_s)) if total_budget_s is not None else None

        def _elapsed_ms() -> float:
            return (time.perf_counter() - t_origin) * 1000

        for candidate in chain:
            if tried >= max_candidates:
                break

            provider = candidate["provider"]
            model = candidate["model"]
            touched += 1
            attempt_no = 1 + touched

            # FIX120 — boundary gate: never outside the allowed providers,
            # and no setting can permit it. Does not consume a slot.
            if outside_boundary(provider, allowed):
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="skipped",
                            reason="outside_boundary", elapsed_ms=_elapsed_ms())
                continue

            # Account-scope gate — this provider already failed at the
            # account level during THIS request (or the primary did).
            # Does not consume an attempt slot.
            if provider in dead_accounts:
                log.info(
                    "fallback_candidate_skipped",
                    candidate_provider=provider,
                    candidate_model=model,
                    reason="account_failed_this_request",
                )
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="skipped",
                            reason="account_failed_this_request", elapsed_ms=_elapsed_ms())
                continue

            # Capability gate — skip a candidate that lacks a required
            # capability. Does NOT consume an attempt slot (mirrors the
            # missing-key skip) so the runner keeps looking for a model
            # that CAN serve the request.
            if not model_meets(required_caps, model):
                log.info(
                    "fallback_candidate_skipped",
                    candidate_provider=provider,
                    candidate_model=model,
                    reason="capability_mismatch",
                )
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="skipped",
                            reason="capability_mismatch", elapsed_ms=_elapsed_ms())
                continue

            # FIX120 — cost-cap gate (price ratio). Off by default.
            cost_reason = cost_blocks_candidate(policy, original_model, model)
            if cost_reason:
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="skipped",
                            reason=f"cost_cap: {cost_reason}", elapsed_ms=_elapsed_ms())
                continue

            # Key check first — a missing key is recorded but does NOT
            # consume one of the ``max_candidates`` real attempt slots.
            # The reason is operator-friendliness: a tenant that has
            # only configured one provider should still be able to
            # benefit from a fallback to a provider whose key IS set,
            # even if it sits further down the chain.
            api_key: str | None = None
            if session is not None:
                api_key = await self.key_service.get_api_key_async(
                    session, provider, model=model
                )
            if not api_key:
                attempts.append(
                    FallbackAttempt(
                        provider=provider,
                        model=model,
                        error_code="no_api_key",
                        error_message=(
                            f"No API key configured for provider '{provider}'."
                        ),
                        latency_ms=0.0,
                        attempted_at=datetime.now(timezone.utc),
                    )
                )
                log.info(
                    "fallback_candidate_skipped",
                    candidate_provider=provider,
                    candidate_model=model,
                    reason="no_api_key",
                )
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="skipped",
                            error_code="no_api_key", reason="no_api_key", elapsed_ms=_elapsed_ms())
                continue

            # Time-budget gate — opening an attempt with (almost) no
            # runway only delays the error the user is already owed.
            remaining_s = (deadline - time.perf_counter()) if deadline is not None else attempt_timeout_s
            if remaining_s < min_runway_s:
                log.warning(
                    "fallback_budget_exhausted",
                    budget_s=total_budget_s,
                    attempts=len(attempts),
                    tried=tried,
                )
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="skipped",
                            reason="budget_exhausted", elapsed_ms=_elapsed_ms())
                log_outcome(log, path=path, requested_model=original_model, outcome="budget_exhausted",
                            attempts=tried, time_spent_s=time.perf_counter() - t_walk)
                break

            tried += 1
            t0 = time.perf_counter()
            route = RouteDecision(provider_name=provider, model_name=model)
            try:
                response = await asyncio.wait_for(
                    self.dispatcher.dispatch(
                        route=route,
                        payload=request,
                        messages=messages,
                        api_key=api_key,
                    ),
                    timeout=min(attempt_timeout_s, remaining_s),
                )
            except asyncio.TimeoutError as exc:
                latency_ms = (time.perf_counter() - t0) * 1000
                last_error = ProviderError(
                    f"Fallback to '{provider}' timed out.",
                    code="provider_timeout",
                    details={"provider": provider, "model": model},
                )
                attempts.append(
                    FallbackAttempt(
                        provider=provider,
                        model=model,
                        error_code="provider_timeout",
                        error_message=str(exc)[:200] or "timeout",
                        latency_ms=latency_ms,
                        attempted_at=datetime.now(timezone.utc),
                    )
                )
                # A timeout is account-scoped (the box, not one model):
                # skip this provider's other candidates and mark the
                # pair + provider-wide health keys for later requests.
                dead_accounts.add(provider)
                await mark_provider_unhealthy(
                    provider, model, error_code="provider_timeout", policy=policy
                )
                log.warning(
                    "fallback_candidate_failed",
                    candidate_provider=provider,
                    candidate_model=model,
                    error_code="provider_timeout",
                    failure_scope="account",
                    latency_ms=int(latency_ms),
                )
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="timeout",
                            error_code="provider_timeout", latency_ms=latency_ms, elapsed_ms=_elapsed_ms())
                continue
            except GatewayError as exc:
                latency_ms = (time.perf_counter() - t0) * 1000
                last_error = exc
                code = getattr(exc, "code", "provider_error")
                scope = classify_failure_scope(exc, policy)
                attempts.append(
                    FallbackAttempt(
                        provider=provider,
                        model=model,
                        error_code=code,
                        error_message=str(exc)[:200],
                        latency_ms=latency_ms,
                        attempted_at=datetime.now(timezone.utc),
                    )
                )
                if is_fallback_eligible(exc, policy):
                    # Pair-scoped marker (FIX49 Part C); widens to the
                    # provider automatically for _PROVIDER_WIDE_CODES.
                    await mark_provider_unhealthy(provider, model, error_code=code, policy=policy)
                log.warning(
                    "fallback_candidate_failed",
                    candidate_provider=provider,
                    candidate_model=model,
                    error_code=code,
                    failure_scope=scope,
                    latency_ms=int(latency_ms),
                )
                log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                            provider=provider, model=model, outcome="failed",
                            error_code=code, reason=f"scope={scope}", latency_ms=latency_ms,
                            elapsed_ms=_elapsed_ms())
                if scope == "request":
                    # The request itself is doomed (too long, rejected,
                    # policy) — every further vendor refuses it the same
                    # way. Stop and surface this error now.
                    break
                if scope == "account":
                    dead_accounts.add(provider)
                continue

            # Success — clear any stale health marker on this provider
            # so the streaming pre-check doesn't keep skipping it.
            await clear_provider_health(provider)

            # Success — attempt_number counts the primary as 1, so the
            # first fallback that succeeds is 2.
            attempt_number = 1 + len(attempts) + 1
            latency_ms = (time.perf_counter() - t0) * 1000
            log.info(
                "fallback_candidate_success",
                candidate_provider=provider,
                candidate_model=model,
                attempt_number=attempt_number,
                prior_attempts=len(attempts),
            )
            log_attempt(log, path=path, requested_model=original_model, attempt=attempt_no,
                        provider=provider, model=model, outcome="success",
                        latency_ms=latency_ms, elapsed_ms=_elapsed_ms())
            log_outcome(log, path=path, requested_model=original_model, outcome="rescued",
                        attempts=tried, time_spent_s=time.perf_counter() - t_walk,
                        served_model=model, served_provider=provider)
            return FallbackResult(
                response=response,
                provider_used=provider,
                model_used=model,
                attempt_number=attempt_number,
                attempts=attempts,
            )

        # All candidates exhausted. Re-raise the last error so the
        # caller's error path runs unchanged. attempts is dropped on the
        # raise — the orchestrator can still populate fallback_attempts
        # on the failed request_log row by inspecting ``attempts`` if it
        # caught a reference earlier; in practice the failure surface
        # uses last_error.code.
        log.warning(
            "fallback_exhausted",
            attempts=len(attempts),
            tried=tried,
        )
        log_outcome(log, path=path, requested_model=original_model, outcome="exhausted",
                    attempts=tried, time_spent_s=time.perf_counter() - t_walk)
        if last_error is not None:
            raise last_error
        raise ProviderError(
            "Fallback exhausted with no candidates attempted.",
            details={
                "original_provider": original_provider,
                "original_model": original_model,
            },
        )

    def _streaming_walk(
        self,
        *,
        original_provider: str,
        original_model: str,
        policy: dict | None,
        action: str,
        allowed_providers: Iterable[str] | None,
        skipped: list[dict] | None,
    ) -> tuple[list[dict[str, str]], int, frozenset[str] | None, dict]:
        """Shared prologue of the two streaming selectors: the chain for
        the action, the attempt ceiling, the boundary and the policy."""
        pol = policy or policy_mod.base_policy()
        if pol is policy_mod.base_policy() and action in ("cooldown_then_next", "next_in_chain"):
            chain = get_fallback_chain(original_model, original_provider)
        else:
            chain = chain_for_action(action, original_model, original_provider, policy=pol)
        lim = self._limits(pol, "stream")
        return chain, int(lim["max_candidates"]), allowed_set(pol, allowed_providers), pol

    @staticmethod
    def _skip(skipped: list[dict] | None, provider: str, model: str, reason: str) -> None:
        if skipped is not None:
            skipped.append({"provider": provider, "model": model, "reason": reason})

    def select_streaming_candidate(
        self,
        *,
        original_provider: str,
        original_model: str,
        session: "Session | None",
        skip_providers: set[str] | None = None,
        skip_pairs: "set[tuple[str, str]] | None" = None,
        required: "RequiredCapabilities | None" = None,
        policy: dict | None = None,
        action: str = "cooldown_then_next",
        allowed_providers: Iterable[str] | None = None,
        skipped: list[dict] | None = None,
    ) -> dict | None:
        """Pick the first viable streaming fallback for a primary.

        Streaming can't reuse :meth:`run` because that method completes
        a non-streaming dispatch. The streaming branch needs the
        chain-walk + key-check logic without actually firing the
        request. Returns ``{"provider", "model", "api_key"}`` on the
        first candidate that:

          1. Has an API key configured for the tenant (skipped
             without consuming a slot when missing).
          2. Has an adapter registered with the dispatcher AND
             ``supports_streaming=True`` (a non-streaming adapter
             would force the rare and lossy mid-stream swap).
          3. Is NOT in ``skip_providers`` (used by the in-generator
             error path to avoid re-trying a candidate that just
             failed this same request).
          4. FIX120 — is inside the request's allowed providers and the
             cost cap; the kill switch returns ``None`` before any of it.

        Returns ``None`` when the chain is exhausted — callers should
        treat that as "stay with the primary and let the existing
        error path fire". ``skipped`` (when given) collects every
        candidate passed over, with the reason, for the attempt log.
        """
        if session is None:
            return None
        if not policy_mod.is_enabled():
            return None

        skip = skip_providers or set()
        pairs = skip_pairs or set()
        chain, max_candidates, allowed, pol = self._streaming_walk(
            original_provider=original_provider, original_model=original_model,
            policy=policy, action=action, allowed_providers=allowed_providers, skipped=skipped,
        )
        tried = 0

        for candidate in chain:
            if tried >= max_candidates:
                break
            provider = candidate["provider"]
            model = candidate["model"]
            if outside_boundary(provider, allowed):
                self._skip(skipped, provider, model, "outside_boundary")
                continue
            if provider in skip:
                self._skip(skipped, provider, model, "account_failed_this_request")
                continue
            # FIX49 Part C — a pair that already failed THIS request is
            # out; the provider's other models stay in the running.
            if (provider, model) in pairs:
                self._skip(skipped, provider, model, "pair_failed_this_request")
                continue

            # Capability gate — don't stream a vision/tools request from
            # a model that can't serve it.
            if required is not None:
                from app.services.capability_detection import model_meets

                if not model_meets(required, model):
                    self._skip(skipped, provider, model, "capability_mismatch")
                    continue

            cost_reason = cost_blocks_candidate(pol, original_model, model)
            if cost_reason:
                self._skip(skipped, provider, model, f"cost_cap: {cost_reason}")
                continue

            api_key = self.key_service.get_api_key(session, provider, model=model)
            if not api_key:
                # No key → skip without consuming a real attempt slot,
                # mirroring the run() loop's defensive contract.
                self._skip(skipped, provider, model, "no_api_key")
                continue

            adapter = self.dispatcher.adapters.get(provider)
            if adapter is None or not getattr(adapter, "supports_streaming", False):
                # Adapter exists but can't stream — keep walking the
                # chain so we don't fall back from streaming to
                # buffered + a header lie.
                self._skip(skipped, provider, model, "adapter_cannot_stream")
                continue

            tried += 1
            return {
                "provider": provider,
                "model": model,
                "api_key": api_key,
            }
        return None

    async def select_streaming_candidate_async(
        self,
        *,
        original_provider: str,
        original_model: str,
        session: "AsyncSession | None",
        skip_providers: set[str] | None = None,
        required: "RequiredCapabilities | None" = None,
        policy: dict | None = None,
        action: str = "cooldown_then_next",
        allowed_providers: Iterable[str] | None = None,
        skipped: list[dict] | None = None,
    ) -> dict | None:
        """Async twin of :meth:`select_streaming_candidate`.

        The streaming **pre-check** runs while the request's AsyncSession is
        still open and on the event loop, so it must resolve the candidate
        key via ``get_api_key_async`` (awaited) — passing that async session
        to the sync :meth:`select_streaming_candidate` made
        ``AsyncSession.execute()`` return an un-awaited coroutine and blew up
        with ``'coroutine' object has no attribute 'scalars'`` whenever a
        provider was marked unhealthy. The in-generator error path keeps
        using the SYNC variant because its session is already closed and it
        mints a short-lived sync one instead.
        """
        if session is None:
            return None
        if not policy_mod.is_enabled():
            return None

        skip = skip_providers or set()
        chain, max_candidates, allowed, pol = self._streaming_walk(
            original_provider=original_provider, original_model=original_model,
            policy=policy, action=action, allowed_providers=allowed_providers, skipped=skipped,
        )
        tried = 0

        for candidate in chain:
            if tried >= max_candidates:
                break
            provider = candidate["provider"]
            model = candidate["model"]
            if outside_boundary(provider, allowed):
                self._skip(skipped, provider, model, "outside_boundary")
                continue
            if provider in skip:
                self._skip(skipped, provider, model, "account_failed_this_request")
                continue

            # FIX49 Part C — never fall back INTO a (provider, model)
            # pair the health marker says just failed: the old
            # provider-level marker made the pre-check pick a fallback
            # on the same dead account and fail twice. A marked pair
            # (or a provider-wide infra marker) is skipped; a healthy
            # sibling model on the same provider stays viable.
            if await is_provider_unhealthy(provider, model):
                self._skip(skipped, provider, model, "health_marker")
                continue

            if required is not None:
                from app.services.capability_detection import model_meets

                if not model_meets(required, model):
                    self._skip(skipped, provider, model, "capability_mismatch")
                    continue

            cost_reason = cost_blocks_candidate(pol, original_model, model)
            if cost_reason:
                self._skip(skipped, provider, model, f"cost_cap: {cost_reason}")
                continue

            api_key = await self.key_service.get_api_key_async(
                session, provider, model=model
            )
            if not api_key:
                self._skip(skipped, provider, model, "no_api_key")
                continue

            adapter = self.dispatcher.adapters.get(provider)
            if adapter is None or not getattr(adapter, "supports_streaming", False):
                self._skip(skipped, provider, model, "adapter_cannot_stream")
                continue

            tried += 1
            return {
                "provider": provider,
                "model": model,
                "api_key": api_key,
            }
        return None


# ---------------------------------------------------------------------------
# Eligibility classifier
# ---------------------------------------------------------------------------

# Error codes that the gateway orchestrator treats as fallback-eligible.
# FIX120 — derived from the policy table: every code whose row is a
# walking action under the SHIPPED policy (opt-in chains off). Kept as a
# name because three modules and the request_log docstring refer to it.
# Note: ``rate_limit_exceeded`` is the upstream provider's 429 mapped
# by ``error_handler.map_provider_error``. The gateway's own per-key
# rate-limit (raised earlier in the pipeline) shares the class but
# never reaches the dispatch block, so this set is safe in context.
FALLBACK_ELIGIBLE_ERRORS: set[str] = set(policy_mod.eligible_error_codes())


def is_fallback_eligible(exc: GatewayError, policy: dict | None = None) -> bool:
    """Return True if ``exc`` should trigger the fallback chain.

    FIX120 — the answer is the policy table's: a walking action for the
    exception's code under ``policy`` (the shipped file when ``None``).
    """
    code = getattr(exc, "code", None)
    if not code:
        return False
    return decide(error_code=code, policy=policy).walks
