"""Observability domain contracts.

These types define the internal representation of request lifecycle events,
provider calls, usage tracking, and audit records.

Most fields are privacy-safe (counts, IDs, timing). Trace snapshots are
stored only after DLP-safe substitution; raw blocked prompts and raw matched
values must never be persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RequestStatus(str, Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    PROVIDER_ERROR = "provider_error"
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    INTERNAL_ERROR = "internal_error"


@dataclass
class RequestRecord:
    """Complete record of a single request through the gateway.

    Most fields are privacy-safe metadata. DLP trace fields are expected to
    carry masked/redacted snapshots only; raw user content belongs in memory
    for the duration of a request, not in persisted request logs.
    """

    request_id: str
    tenant_id: str
    project_id: str
    provider: str = ""
    model: str = ""
    status: RequestStatus = RequestStatus.SUCCESS
    policy_action: str = "allow"

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    latency_ms: int = 0

    # Usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # Reasoning / thinking token accounting (T3). Subdivision of
    # ``completion_tokens`` for o-series-style providers; folded into
    # ``output_tokens`` and length-estimated for Anthropic. Both
    # default to 0 so any non-reasoning call site that constructs a
    # ``RequestRecord`` continues to work unchanged.
    reasoning_tokens: int = 0
    reasoning_cost: float = 0.0

    # Routing
    route_provider: str = ""
    route_model: str = ""
    fallback_used: bool = False
    constrained_by_policy: bool = False

    # DLP
    dlp_findings_count: int = 0
    dlp_direction: str = ""
    # Per-direction summary, persisted to request_logs by RequestLogService.
    dlp_input_findings: int = 0
    dlp_input_action: str | None = None
    dlp_output_findings: int = 0
    dlp_categories: list[str] = field(default_factory=list)
    # Which router won: "semantic" / "keyword" / "service" / "auto" / None.
    router_used: str | None = None
    # FIX35 Part C — the Auto router's task class (task_taxonomy.json id);
    # None when the caller named a model. Feeds the per-tenant ranking.
    task_category: str | None = None
    # Effective policy profile applied — project default OR a
    # per-request ``policy_profile_id`` override. Mirrors
    # RequestLogORM.policy_profile_id (migration 020).
    policy_profile_id: str | None = None

    # Error
    error_code: str | None = None
    error_message: str | None = None

    # DLP trace (migration 021). Persist only DLP-safe snapshots.
    # All five default to None so existing call sites continue to compile
    # without touching them; the orchestrator fills them in at the build
    # sites that have the necessary state in scope.
    input_raw: str | None = None
    input_masked: str | None = None
    output_raw: str | None = None
    output_final: str | None = None
    dlp_findings_json: str | None = None
    # AES-256-GCM ciphertext of the raw user prompt (stage 1 of the
    # 4-stage trace). See ``request_log.py`` column docstring.
    input_raw_encrypted: str | None = None

    # Phase-2 fallback routing. ``provider`` / ``model`` describe what
    # *answered*; ``original_provider`` / ``original_model`` describe
    # what the routing engine originally picked. Equal on non-fallback
    # requests (set by the orchestrator on every success path).
    # ``fallback_used`` is already declared above as a legacy field —
    # the Phase-2 wiring just starts populating it for real.
    # List of attempt dicts (``[{"provider","model","error_code",...}]``).
    # Pass the list directly — the ``RequestLogORM.fallback_attempts``
    # JSONB column handles serialization. JSON-encoding here would
    # cause double-encoding (`"\"[...]\""`) once SQLAlchemy serializes
    # again. The gRPC client (``log_worker_client``) json-encodes at
    # the wire boundary.
    fallback_attempts: list[dict] | None = None
    original_provider: str | None = None
    original_model: str | None = None

    # Portal user attribution (migration 062) — resolved by the auth
    # layer (api_keys.portal_user_id first, trusted X-User-ID second)
    # and threaded through RequestContext.user_id. None when the caller
    # isn't bound to a portal user (admin/tenant keys, admin JWT).
    # Declared LAST so every positional call site keeps working.
    user_id: str | None = None

    def complete(
        self,
        status: RequestStatus = RequestStatus.SUCCESS,
        latency_ms: int = 0,
    ) -> None:
        self.status = status
        self.completed_at = datetime.now(timezone.utc)
        self.latency_ms = latency_ms

    def to_dict(self) -> dict:
        """Serialize for persistence or logging."""
        return {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status.value if isinstance(self.status, RequestStatus) else self.status,
            "policy_action": self.policy_action,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "fallback_used": self.fallback_used,
            "constrained_by_policy": self.constrained_by_policy,
            "dlp_findings_count": self.dlp_findings_count,
            "error_code": self.error_code,
        }


# FIX35 Part B — ids that name a ROUTER, not a model. A request routed to
# one of these is answered by whatever model the upstream meta-router
# picked, and only the provider's response says which. ``"auto"`` is the
# bare sentinel the cross-provider fallback chain hands OpenRouter
# (model_tiers.TIERS); ``"openrouter/auto"`` is the same router addressed
# explicitly by a caller; any other ``<vendor>/auto`` follows the same
# convention.
META_ROUTER_MODELS: frozenset[str] = frozenset({"auto", "openrouter/auto"})


def is_meta_router_model(model: str | None) -> bool:
    """True when ``model`` is a router id whose answer comes from a model
    the response must name (OpenRouter: "the response's ``model`` field
    tells you which model was selected")."""
    if not model:
        return False
    return model in META_ROUTER_MODELS or model.endswith("/auto")


def served_model_name(routed: str, reported: str | None) -> str:
    """The model name a RESPONSE must carry — the one that actually ran.

    * A concrete routed id stays verbatim. Provider-reported names carry
      date suffixes (``gpt-4o-mini-2024-07-18``) that would fragment
      per-model aggregation and confuse a caller who asked for the
      alias, so the alias the gateway resolved wins.
    * A meta-router id (``auto`` sentinel, ``openrouter/auto``) is never
      returned when the provider reported a concrete model: the caller
      asked a router, the response names the model the router chose.
    * A meta-router id with NO report yet (before the first chunk, or a
      provider that omits ``model``) is normalised to the router's own
      public id (``openrouter/auto``) so the bare fallback sentinel
      ``"auto"`` — meaningless to a caller — never reaches the wire.
    """
    if is_meta_router_model(routed):
        if reported and not is_meta_router_model(reported):
            return reported
        return "openrouter/auto" if routed == "auto" else routed
    return routed


def resolved_log_model(routed: str, reported: str | None) -> str:
    """The model name to persist on ``request_logs.model``.

    Routing always resolves the caller's ``auto`` before dispatch —
    EXCEPT the cross-provider fallback chain, whose OpenRouter
    candidate is the literal ``"auto"`` sentinel (OpenRouter's own
    meta-router decides server-side), and an explicit
    ``openrouter/auto`` request. Recording a router id makes the usage
    breakdown meaningless ("auto" rows). The provider RESPONSE reports
    what actually answered, so prefer it for the router-id case only.
    Concrete routed ids stay verbatim — provider-reported names carry
    date suffixes (gpt-4o-mini-2024-07-18) that would fragment
    per-model aggregation. (FIX35 Part B widened this from the bare
    ``"auto"`` sentinel to every meta-router id.)
    """
    if is_meta_router_model(routed) and reported and not is_meta_router_model(reported):
        return reported
    return routed


# ---------------------------------------------------------------------------
# DLP trace helpers
# ---------------------------------------------------------------------------


def extract_messages_text(messages: list[Any]) -> str:
    """Join all message content strings into one trace-friendly blob.

    Accepts either dicts (``{"role": ..., "content": ...}``) or pydantic
    Message instances. ``content`` may be a plain string or a list of
    block dicts (OpenAI vision format) — only ``type == "text"`` blocks
    contribute. Roles are joined with a ``\\n---\\n`` separator so the
    rendered trace in the admin UI shows clear message boundaries.
    """
    parts: list[str] = []
    for m in messages:
        if hasattr(m, "model_dump"):
            m = m.model_dump()
        if not isinstance(m, dict):
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n---\n".join(parts)


def build_dlp_findings_json(
    input_match_actions: list[Any],
    output_match_actions: list[Any] | None = None,
    span_map: dict[str, str] | None = None,
) -> str | None:
    """Serialize DLP match actions to a JSON list for the trace column.

    Returns ``None`` when there are no findings to record (so we don't
    write empty arrays into a column whose absence already means "no
    findings"). Each entry is a flat dict — see the spec in
    ``021_add_dlp_trace_to_request_logs.py`` for the shape.

    The ``span_map`` carries the actual indexed tokens emitted by the
    transformer (``{"[EMAIL_0]": "alice@example.com"}``); we reverse it
    so each finding can record the exact token that replaced its raw
    value. When a match isn't in the span_map (e.g. the action was
    ALLOW or BLOCK, or the value duplicated and lost the lookup), we
    fall back to ``None`` for ``mask_token`` rather than guess.
    """
    output_match_actions = output_match_actions or []
    span_map = span_map or {}
    # Reverse map: original value → indexed token. Multiple originals
    # may collide on the same value; last writer wins, which is fine
    # for trace purposes (operator can still see what was masked).
    value_to_token: dict[str, str] = {v: k for k, v in span_map.items()}

    findings: list[dict[str, Any]] = []
    for direction, actions in (
        ("input", input_match_actions),
        ("output", output_match_actions),
    ):
        for ma in actions:
            match = ma.match
            raw_text = match.matched_text or ""
            findings.append({
                "direction": direction,
                "detector": match.detector_name,
                "entity_type": match.entity_type,
                "category": match.category,
                "severity": match.severity.name if hasattr(match.severity, "name") else str(match.severity),
                "span_start": match.start,
                "span_end": match.end,
                "matched_text_len": len(raw_text),
                "confidence": match.confidence,
                "action": ma.action.name.lower() if hasattr(ma.action, "name") else str(ma.action),
                "mask_token": value_to_token.get(match.matched_text),
            })

    if not findings:
        return None
    return json.dumps(findings, ensure_ascii=False)


# Phase 17D — stored in ``input_raw`` for clean (no-DLP-finding) traffic so the
# plaintext column never holds verbatim content the detector might have missed.
# The real verbatim lives encrypted in ``input_raw_encrypted``.
INPUT_RAW_NOT_PERSISTED_MARKER = "[NOT_PERSISTED_SEE_ENCRYPTED]"


def safe_input_snapshot(
    input_raw: str | None,
    input_masked: str | None,
    enforcement: Any,
    *,
    blocked_placeholder: str = "[BLOCKED_BY_DLP]",
) -> str | None:
    """Return a DB-safe snapshot for the ``input_raw`` column.

    Stage 1 of the 4-stage trace (User → Service). The raw user input
    contains PII when DLP fires, so we never persist it — only a
    fixed placeholder. Use ``safe_input_masked_snapshot`` for the
    ``input_masked`` column, which keeps the actual masked text
    (tokens only, no PII).

    Phase 17D — even "clean" input (nothing the detector flagged) can
    hold PII the detector MISSED (a false negative). Persisting it
    verbatim would leak that content to anyone who reads the
    ``request_logs`` table, so the clean path now stores a marker, NOT
    the raw text. The verbatim is preserved encrypted in
    ``input_raw_encrypted`` (AES-256-GCM Safe Trace audit channel,
    populated for every request) and the admin Safe Trace view decrypts
    stage 1 from there — so a plain DB leak no longer exposes
    detector-missed content.
    """
    if getattr(enforcement, "is_blocked", False):
        return blocked_placeholder
    if getattr(enforcement, "match_actions", None):
        return "[MASKED_BY_DLP]"
    return INPUT_RAW_NOT_PERSISTED_MARKER


def safe_input_masked_snapshot(
    input_masked: str | None,
    enforcement: Any,
    *,
    blocked_placeholder: str = "[BLOCKED_BY_DLP]",
) -> str | None:
    """Return a DB-safe snapshot for the ``input_masked`` column.

    Stage 2 of the 4-stage trace (Service → AI). By the time the
    transformer has run, ``input_masked`` carries only mask tokens
    like ``[EMAIL_0]`` / ``[NAME_aB3kM9xQ]`` — no raw PII — so it is
    safe to persist verbatim. Falls back to the legacy placeholder
    on the off-chance the caller has no masked text to record (e.g.
    block path that fired before transform).
    """
    if getattr(enforcement, "is_blocked", False):
        return blocked_placeholder
    if input_masked is None:
        return "[MASKED_BY_DLP]" if getattr(enforcement, "match_actions", None) else None
    return input_masked


# Marker stored in ``output_final`` when no provider-safe (masked /
# canonical) representation is available for a request that had DLP
# findings — used INSTEAD of ever persisting the restored display.
OUTPUT_FINAL_REDACTED_MARKER = "[REDACTED_NO_CANONICAL]"


def safe_output_final_snapshot(
    output_masked: str | None,
    enforcement: Any,
    *,
    blocked_placeholder: str = "[BLOCKED_BY_DLP]",
    redacted_marker: str = OUTPUT_FINAL_REDACTED_MARKER,
) -> str | None:
    """Phase 4A.2.3 — DB-safe snapshot for the ``output_final`` column.

    ``output_final`` now stores the PROVIDER-SAFE (post-outbound-DLP,
    PRE-restore, tokenized) representation — the same masked text the
    provider would receive as history — and NEVER the restored display
    the user saw. Callers MUST pass the masked output (``ai_to_service_
    masked`` / the cached masked text / the aggregated masked-provider
    stream text).

    Fallbacks (never display raw):
      - blocked request → ``[BLOCKED_BY_DLP]``.
      - masked text unavailable but DLP fired → ``redacted_marker``.
      - no findings + no masked text → pass through (clean/empty text is
        provider-safe).
    The ``app.jobs.dlp_retention`` job still NULLs this column on its
    retention schedule.
    """
    if getattr(enforcement, "is_blocked", False):
        return blocked_placeholder
    if output_masked is None or output_masked == "":
        if getattr(enforcement, "match_actions", None):
            return redacted_marker
        return output_masked
    return output_masked


def safe_output_snapshot(
    output_raw: str | None,
    output_final: str | None,
    enforcement: Any,
    *,
    blocked_placeholder: str = "[BLOCKED_BY_DLP]",
) -> str | None:
    """DEPRECATED (Phase 4A.2.3) — use :func:`safe_output_final_snapshot`.

    Retained only for signature compatibility. It NO LONGER persists the
    restored display (``output_final`` arg): it returns the provider-safe
    masked arg (``output_raw``) or a redacted marker, so a stray call can
    never leak restored raw into ``output_final``.
    """
    if getattr(enforcement, "is_blocked", False):
        return blocked_placeholder
    del output_final  # restored display is no longer persisted
    return safe_output_final_snapshot(
        output_raw, enforcement, blocked_placeholder=blocked_placeholder
    )


def safe_output_raw_snapshot(
    output_after_outbound_mask: str | None,
    enforcement: Any,
    *,
    blocked_placeholder: str = "[BLOCKED_BY_DLP]",
) -> str | None:
    """Return a DB-safe snapshot for the ``output_raw`` column.

    Stage 3 of the 4-stage trace (AI → Service). The caller must pass
    the response **after the outbound DLP pass** (in the orchestrator
    this is ``ai_to_service_masked``; in the streaming path it's the
    aggregated masked-provider text). That text contains only mask
    tokens — any fresh PII the model emitted has already been
    transformed — so it is safe to persist.
    """
    if getattr(enforcement, "is_blocked", False):
        return blocked_placeholder
    if output_after_outbound_mask is None:
        return "[MASKED_BY_DLP]" if getattr(enforcement, "match_actions", None) else None
    return output_after_outbound_mask


@dataclass(frozen=True)
class UsageRecord:
    """Normalized token usage and cost for a single request."""

    request_id: str
    tenant_id: str
    project_id: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    is_estimate: bool = True  # True when based on heuristic, False when from provider
