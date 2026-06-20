"""Central DLP Policy Engine (Phase 1).

Single shared decision boundary that EVERY chat path routes findings
through before the transformer:

    Detector findings
      → SensitivityClassifier (reused, unchanged)
      → DLPPolicyEngine               ← this module
          → PolicyEvaluator (reused, unchanged)
      → DLPTransformer (reused, unchanged)

Phase 1 scope and guarantees
----------------------------
- The engine is an *orchestration / decision boundary only*. It does
  NOT contain detector logic, regex, masking, or any Smart-Numeric /
  Royal-Registry / Secure-Calculate logic (those are later phases).
- The V2 decision is computed with the SAME classifier + PolicyEvaluator
  the legacy seam already uses, so for the current fixtures the V2 action
  is byte-for-byte identical to the legacy action. Parity is by
  construction, not by re-implementation.
- Two feature flags gate behavior (see ``app.core.config``):
    * ``dlp_policy_engine_v2_shadow``  (default True)
    * ``dlp_policy_engine_v2_enabled`` (default False)
  In the default config the LEGACY decision is the one that takes
  effect; the V2 decision is computed in parallel and compared
  (shadow mode). Flipping ``enabled`` makes V2 authoritative; the flag
  is read on every call so an operator can roll back with a config
  change + restart, no code change.
- Failure policy is deterministic and fail-closed for secrets — see
  ``_fail_safe_decision``. SCOPE: this fail-safe covers exceptions raised
  INSIDE the engine (classifier / PolicyEvaluator / annotation). It does
  NOT cover upstream DETECTION failures — a detector/Rust-sidecar/NER
  failure that returns an empty or partial findings list happens before
  the engine and fails OPEN (the engine simply sees fewer findings).
  The engine cannot distinguish "scan succeeded, nothing found" from
  "scan failed, returned empty". Full fail-closed detection semantics
  remain a separate prerequisite, not delivered by this engine.

Context wiring (Phase 1 status): the ``DLPRequestContext`` contract
exists and is threaded, but only the tool-argument call sites currently
pass real values (source_type / conversation_id / model / channel).
Inbound / outbound / per-chunk scan sites still pass ``None`` (the engine
fills defaults; only ``direction`` is always accurate, derived from the
DetectionResult). tenant/user/project/provider are not yet populated
anywhere. Phase 2 must wire these before any context-based decision.

Logging discipline: NEVER log ``matched_text``. Shadow / security logs
carry only detector_name, entity_type, category, action, span position
or length, and request/channel/direction metadata.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

import structlog

from app.core.config import settings
from app.domain.dlp import (
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    EnforcementDecision,
    MatchAction,
    SensitivityLevel,
)
from app.domain.policy import PolicyProfile
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.numeric_context import (
    CONFIDENTIAL_NUMERIC,
    SECRET_NUMERIC,
    STRONG_IDENTIFIER,
    NumericTextSegment,
    numeric_context_analyzer,
)
from app.services.dlp.public_entity_registry import (
    finding_relaxable_by,
    public_entity_registry,
)
from app.services.policy.evaluator import PolicyEvaluator

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Context contracts (immutable). These intentionally carry NO raw values —
# only ids, role/channel/source enums, and routing metadata.
# ---------------------------------------------------------------------------


class ScanDirection(str, enum.Enum):
    """Direction of the scan. Mirrors ``DetectionResult.scanned_direction``."""

    INPUT = "input"
    OUTPUT = "output"

    @classmethod
    def coerce(cls, value: "str | ScanDirection | None") -> "ScanDirection":
        if isinstance(value, ScanDirection):
            return value
        return cls.OUTPUT if value == "output" else cls.INPUT


class SourceType(str, enum.Enum):
    """Where the scanned text originated.

    Phase 1 does not branch policy on ``source_type`` — it is recorded
    in context + metadata so Phase 2-4 (numeric / royal / secure-calc)
    can specialise without another plumbing pass.
    """

    DIRECT_API = "direct_api"
    WEBCHAT = "webchat"
    TOOL_ARGUMENT = "tool_argument"
    TOOL_RESULT = "tool_result"
    MEMORY = "memory"
    RAG = "rag"
    FILE = "file"
    REASONING = "reasoning"
    SYSTEM_MESSAGE = "system_message"


# Channel constants — kept as plain strings (not an enum) because the
# transport layer already speaks these labels.
CHANNEL_API = "api"
CHANNEL_WEBCHAT = "webchat"
CHANNEL_INTERNAL = "internal"


def _hash_id(value: str | None) -> str | None:
    """Short, stable, non-reversible correlator for an identifier.

    Phase 4A.0: identity fields (tenant/project/key/actor/conversation)
    are IDs, not secrets, but to satisfy "no raw IDs in logs" they are
    emitted as a truncated SHA-256 so log lines stay correlatable without
    exposing the raw value. ``None`` stays ``None``.
    """
    if value is None or value == "":
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class DLPRequestContext:
    """Immutable per-evaluation context.

    Every field is optional so existing call sites can adopt the engine
    incrementally. NOTHING here is a raw sensitive value (no API-key/
    internal secret, no authorization header) — it is safe to serialise
    into structured logs via :meth:`safe_log_dict`, which additionally
    HASHES identifier fields.

    Phase 4A.0 identity fields — ``tenant_id`` / ``project_id`` / ``key_id``
    / ``actor_id`` MUST come from the authenticated server context only;
    ``conversation_id`` is an opaque, untrusted locator (NOT proof of
    ownership). ``user_id`` is retained as an alias for the trusted portal
    user (== ``actor_id`` when a trusted portal user is present).
    """

    tenant_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    key_id: str | None = None
    actor_id: str | None = None
    conversation_id: str | None = None
    conversation_id_source: str = "absent"  # trusted_internal_header|client_metadata|absent
    message_role: str | None = None
    source_type: SourceType = SourceType.DIRECT_API
    channel: str = CHANNEL_API
    streaming: bool = False
    provider: str | None = None
    model: str | None = None
    direction: ScanDirection = ScanDirection.INPUT

    def safe_log_dict(self) -> dict[str, Any]:
        """Return a log-safe view: identifier fields HASHED, enums raw.

        No raw IDs, no secrets, no request text ever leaves here.
        """
        return {
            "tenant_id": _hash_id(self.tenant_id),
            "project_id": _hash_id(self.project_id),
            "key_id": _hash_id(self.key_id),
            "actor_id": _hash_id(self.actor_id),
            "conversation_id": _hash_id(self.conversation_id),
            "conversation_id_source": self.conversation_id_source,
            "message_role": self.message_role,
            "source_type": self.source_type.value,
            "channel": self.channel,
            "streaming": self.streaming,
            "provider": self.provider,
            "model": self.model,
            "direction": self.direction.value,
        }


# Conversation-ID provenance markers (audit only — NEVER ownership).
CONV_SRC_TRUSTED_HEADER = "trusted_internal_header"
CONV_SRC_CLIENT_METADATA = "client_metadata"
CONV_SRC_ABSENT = "absent"


def resolve_authenticated_actor(
    *,
    trusted_portal_user_id: str | None,
    trusted_internal_user_id: str | None = None,
    key_id: str | None,
) -> str | None:
    """Resolve the authenticated actor for a request (single rule).

    Order: (1) trusted portal user id (request passed the internal-service
    secret gate), (2) a trusted internal actor id (same gate), (3) the
    authenticated ``key_id`` for API-key-only requests. Returns ``None``
    only when there is no authenticated key identity at all — callers must
    treat a ``None`` actor as an INVALID context (never substitute a shared
    constant like "anonymous"/"default"). Never derives the actor from raw
    headers, request body, email/display name, or tenant_id.
    """
    portal = (trusted_portal_user_id or "").strip()
    if portal:
        return portal
    internal = (trusted_internal_user_id or "").strip()
    if internal:
        return internal
    key = (key_id or "").strip()
    return key or None


def build_dlp_request_context(
    *,
    auth,
    direction: "ScanDirection | str",
    source_type: SourceType = SourceType.DIRECT_API,
    streaming: bool = False,
    model: str | None = None,
    message_role: str | None = None,
    client_conversation_id: str | None = None,
) -> DLPRequestContext:
    """Central, single-rule builder for :class:`DLPRequestContext`.

    Consumes the already-resolved authenticated identity (``auth`` =
    ``ResolvedApiKey``) — NEVER raw headers/body. Tenant/project/key come
    straight from the authenticated key; actor via the single
    ``resolve_authenticated_actor`` rule; channel is webchat when the
    request proved the internal-service secret (audit metadata only — NOT
    a security principal). ``conversation_id`` prefers the trusted
    ``X-Conversation-ID`` (web-chat) and otherwise falls back to the
    client-supplied locator; provenance is recorded but is never ownership.
    No DB/Redis/network; no logging; does not mutate the request.
    """
    tenant_id = getattr(auth, "tenant_id", None) if auth else None
    project_id = getattr(auth, "project_id", None) if auth else None
    key_id = getattr(auth, "key_id", None) if auth else None
    portal_user_id = getattr(auth, "portal_user_id", None) if auth else None
    internal_trusted = bool(getattr(auth, "internal_trusted", False)) if auth else False
    trusted_conv = getattr(auth, "trusted_conversation_id", None) if auth else None

    actor_id = resolve_authenticated_actor(
        trusted_portal_user_id=portal_user_id, key_id=key_id
    )

    if trusted_conv:
        conversation_id = trusted_conv
        conv_src = CONV_SRC_TRUSTED_HEADER
    elif client_conversation_id:
        conversation_id = client_conversation_id
        conv_src = CONV_SRC_CLIENT_METADATA
    else:
        conversation_id = None
        conv_src = CONV_SRC_ABSENT

    # Channel/source default to the caller-provided values, but trusted
    # internal traffic (chat-edge) is labelled WEBCHAT unless the caller
    # already set a more specific source (tool_argument / reasoning).
    resolved_source = source_type
    channel = CHANNEL_API
    if internal_trusted:
        channel = CHANNEL_WEBCHAT
        if source_type == SourceType.DIRECT_API:
            resolved_source = SourceType.WEBCHAT

    return DLPRequestContext(
        tenant_id=tenant_id,
        user_id=portal_user_id,
        project_id=project_id,
        key_id=key_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        conversation_id_source=conv_src,
        message_role=message_role,
        source_type=resolved_source,
        channel=channel,
        streaming=streaming,
        model=model,
        direction=ScanDirection.coerce(direction),
    )


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Rich result of a policy evaluation.

    ``decision`` is the authoritative :class:`EnforcementDecision` that
    callers should act on. The remaining fields are observability /
    Phase-2 hooks.
    """

    decision: EnforcementDecision
    context: DLPRequestContext
    classified_matches: list[DetectionMatch] = field(default_factory=list)
    engine_version: str = "legacy"  # "legacy" | "v2"
    shadow_divergences: int = 0
    used_failsafe: bool = False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

# Categories that must fail CLOSED (block) if the engine cannot produce a
# trustworthy decision. Mirrors the orchestrator's
# ``_NEVER_RESTORE_CATEGORIES`` — secrets/credentials never pass through.
_FAIL_CLOSED_CATEGORIES = frozenset({"secret", "credential"})

# Namespace key for the Phase-1 metadata contract on DetectionMatch.
POLICY_V2_METADATA_KEY = "policy_v2"


class _NumericOversizeError(Exception):
    """Raised internally when a segment exceeded the analysis ceiling and
    authoritative mode must fail closed rather than assume it is clean."""


# Explicit action precedence — most protective wins. Does NOT rely on the
# EnforcementAction enum's integer values (those could be reordered); a
# dedicated rank map makes the ordering a tested, intentional contract.
_ACTION_RANK = {
    EnforcementAction.ALLOW: 0,
    EnforcementAction.RESTORE: 1,
    EnforcementAction.MASK: 2,
    EnforcementAction.REDACT: 3,
    EnforcementAction.BLOCK: 4,
}


def _public_span_safely_contains_finding(public_span, finding) -> bool:
    """True only when ``public_span`` fully contains a non-empty
    ``finding`` span (Phase 3.1 strict containment).

    - finding span must be non-empty (start < end);
    - public span must start at or before the finding and end at or after
      it (public_span ⊇ finding).
    This rejects: partial overlaps, and findings that extend beyond the
    public span (public alias glued to a private prefix/suffix) — those
    keep their masking. message_index match and entity-type compatibility
    are checked by the caller.
    """
    return (
        finding.start < finding.end
        and public_span.start <= finding.start
        and finding.end <= public_span.end
    )


def most_protective_action(actions) -> EnforcementAction:
    """Return the most protective action from ``actions``.

    Order: BLOCK > REDACT > MASK > RESTORE > ALLOW. An empty input is
    ALLOW. An UNKNOWN action (not in the rank map) fails CLOSED → BLOCK,
    so a future enum member can never silently degrade to pass-through.
    """
    best = EnforcementAction.ALLOW
    best_rank = _ACTION_RANK[EnforcementAction.ALLOW]
    for a in actions:
        rank = _ACTION_RANK.get(a)
        if rank is None:
            return EnforcementAction.BLOCK  # unknown → fail closed
        if rank > best_rank:
            best, best_rank = a, rank
    return best


class DLPPolicyEngine:
    """Shared decision boundary wrapping the existing classifier + evaluator.

    Construct once and share across the request lifecycle. Stateless per
    call; feature flags are read from ``settings`` on every ``evaluate``
    so toggling them takes effect on the next request (config-only
    rollback, no redeploy).
    """

    def __init__(
        self,
        classifier: SensitivityClassifier | None = None,
        evaluator: PolicyEvaluator | None = None,
    ) -> None:
        self._classifier = classifier or SensitivityClassifier()
        self._evaluator = evaluator or PolicyEvaluator()

    # -- public API ---------------------------------------------------------

    def evaluate(
        self,
        *,
        text: str,
        findings: list[DetectionMatch],
        direction: "str | ScanDirection",
        context: DLPRequestContext,
        profile: PolicyProfile,
        segments: "list[NumericTextSegment] | None" = None,
    ) -> PolicyEvaluationResult:
        """Evaluate findings and return a :class:`PolicyEvaluationResult`.

        ``segments`` are the per-message text units the V2 numeric
        analyzer runs on (one per message, with ``message_index``). When
        omitted, a single segment is built from ``text`` (message_index
        None — outbound / stream chunk / tool-arg).

        Exception boundaries (Phase 2A.1):
        - LEGACY classify/evaluate failure → Phase 1 fail-safe (unchanged).
        - V2 (analyzer / refinement) failure → SEPARATE boundary:
          * shadow: fall back to the legacy decision, log
            ``dlp_policy_v2_shadow_failed`` — legacy behavior is untouched;
          * enabled + numeric content present: FAIL CLOSED (never raw
            pass-through); enabled + no digits: legacy decision is safe.
        """
        scan_dir = ScanDirection.coerce(direction)
        shadow = bool(getattr(settings, "dlp_policy_engine_v2_shadow", True))
        v2_enabled = bool(getattr(settings, "dlp_policy_engine_v2_enabled", False))

        if segments is None:
            segments = [
                NumericTextSegment(
                    message_index=None,
                    role=getattr(context, "message_role", None),
                    text=text or "",
                    source_type=context.source_type.value,
                )
            ]

        # ---- LEGACY decision (Phase 1 fail-safe still wraps this) --------
        try:
            classified = self._classify(findings)
            result = DetectionResult(
                matches=classified, scanned_direction=scan_dir.value
            )
            legacy_decision = self._evaluator.evaluate(result, profile)
        except Exception as exc:  # noqa: BLE001
            decision = self._fail_safe_decision(findings, profile)
            logger.error(
                "dlp_policy_engine_failed_closed",
                error=str(exc)[:200],
                finding_count=len(findings),
                overall_action=decision.overall_action.name.lower(),
                **context.safe_log_dict(),
            )
            return PolicyEvaluationResult(
                decision=decision, context=context,
                classified_matches=list(findings),
                engine_version="failsafe", used_failsafe=True,
            )

        # ---- V2 decision (separate exception boundary) -------------------
        v2_decision: EnforcementDecision | None = None
        divergences = 0
        has_text = any(getattr(s, "text", "") for s in segments)
        if (classified or has_text) and (v2_enabled or shadow):
            try:
                v2_decision, truncated = self._evaluate_v2(
                    result, profile, context,
                    segments=segments, direction=scan_dir.value,
                )
                divergences = self._shadow_compare(
                    legacy_decision, v2_decision, context, shadow=shadow
                )
                if truncated and v2_enabled:
                    # Numeric content beyond the analysis ceiling could not
                    # be cleared — fail closed rather than pass it raw.
                    raise _NumericOversizeError("segment exceeded analysis ceiling")
            except _NumericOversizeError as exc:
                if v2_enabled:
                    decision = self._numeric_fail_closed_decision(profile)
                    logger.error(
                        "dlp_policy_v2_enabled_failed_closed",
                        reason="oversize", error=str(exc)[:120],
                        **context.safe_log_dict(),
                    )
                    return PolicyEvaluationResult(
                        decision=decision, context=context,
                        classified_matches=classified,
                        engine_version="v2_failsafe", used_failsafe=True,
                    )
                v2_decision = None
            except Exception as exc:  # noqa: BLE001 — V2-only failure
                if v2_enabled:
                    if self._segments_have_digit(segments):
                        decision = self._numeric_fail_closed_decision(profile)
                        logger.error(
                            "dlp_policy_v2_enabled_failed_closed",
                            reason="analyzer_exception", error=str(exc)[:120],
                            **context.safe_log_dict(),
                        )
                        return PolicyEvaluationResult(
                            decision=decision, context=context,
                            classified_matches=classified,
                            engine_version="v2_failsafe", used_failsafe=True,
                        )
                    # No numeric content → legacy decision is safe.
                    logger.warning(
                        "dlp_policy_v2_enabled_failed_no_numeric",
                        error=str(exc)[:120], **context.safe_log_dict(),
                    )
                    v2_decision = legacy_decision
                else:
                    # SHADOW: V2 failure must NOT change the authoritative
                    # legacy decision. Log sanitized + fall back to legacy.
                    logger.warning(
                        "dlp_policy_v2_shadow_failed",
                        error=str(exc)[:120], **context.safe_log_dict(),
                    )
                    v2_decision = None

        if v2_enabled:
            active = v2_decision if v2_decision is not None else legacy_decision
            version = "v2"
        else:
            active = legacy_decision
            version = "legacy"

        return PolicyEvaluationResult(
            decision=active, context=context, classified_matches=classified,
            engine_version=version, shadow_divergences=divergences,
            used_failsafe=False,
        )

    @staticmethod
    def _segments_have_digit(segments) -> bool:
        return any(
            any(ch.isdigit() for ch in (getattr(s, "text", "") or ""))
            for s in segments
        )

    @staticmethod
    def _numeric_fail_closed_decision(profile) -> EnforcementDecision:
        """Hard BLOCK decision for an authoritative V2 failure on
        numeric-containing content. Used instead of the finding-based
        fail-safe because the sensitive content may be a SUPPLEMENTAL
        numeric finding (e.g. OTP) the analyzer didn't get to produce —
        so blocking the whole payload is the only no-raw-egress option.
        """
        synthetic = DetectionMatch(
            detector_name="numeric_v2_fail_closed",
            entity_type="numeric_v2_fail_closed",
            category="secret",
            start=0, end=0, matched_text="",
            severity=SensitivityLevel.CRITICAL,
        )
        return EnforcementDecision(
            match_actions=[MatchAction(
                match=synthetic,
                action=EnforcementAction.BLOCK,
                reason_code="numeric_v2_fail_closed",
                reason_message=(
                    "V2 numeric analysis failed on numeric content; "
                    "blocked to prevent raw pass-through."
                ),
            )],
            policy_profile_id=getattr(profile, "profile_id", "default"),
        )

    def evaluate_decision(
        self,
        *,
        detection_result: DetectionResult,
        profile: PolicyProfile,
        context: DLPRequestContext | None = None,
        text: str = "",
        segments: "list[NumericTextSegment] | None" = None,
    ) -> EnforcementDecision:
        """Drop-in replacement for the legacy classify+evaluate seam.

        Preserves the legacy side effect of writing the classified
        matches back onto ``detection_result.matches``. ``segments``
        (per-message) or ``text`` (single-text) feed the V2 numeric
        analyzer; with neither, V2 does no numeric work (zero behavior
        change). Defaults keep all existing callers working.
        """
        ctx = context or DLPRequestContext(
            direction=ScanDirection.coerce(detection_result.scanned_direction)
        )
        result = self.evaluate(
            text=text,
            findings=detection_result.matches,
            direction=detection_result.scanned_direction,
            context=ctx,
            profile=profile,
            segments=segments,
        )
        detection_result.matches = result.classified_matches
        return result.decision

    # -- internals ----------------------------------------------------------

    def _classify(self, findings: list[DetectionMatch]) -> list[DetectionMatch]:
        if not findings:
            return []
        return self._classifier.classify(findings)

    def _evaluate_v2(
        self,
        result: DetectionResult,
        profile: PolicyProfile,
        context: DLPRequestContext,
        *,
        segments: "list[NumericTextSegment]",
        direction: str = "input",
    ) -> "tuple[EnforcementDecision, bool]":
        """V2 path: Phase 2A.1 per-segment Smart Numeric Masking.

        Returns (decision, truncated). Runs the analyzer over the
        per-message ``segments`` (local offsets + message_index), evaluates
        the combined enriched + supplemental set, then applies a V2-only
        numeric action refinement (most-protective wins): SECRET_NUMERIC →
        REDACT; STRONG_IDENTIFIER / CONFIDENTIAL_NUMERIC → at least MASK.
        Legacy decisions are unaffected.
        """
        enriched, supplemental, truncated = numeric_context_analyzer.analyze(
            segments, result.matches, direction=direction, context=context
        )
        v2_matches = list(enriched) + list(supplemental)
        # Phase 3 — span-scoped public-entity evidence. Tag relaxable
        # name/place/org findings that overlap a known public entity span.
        v2_matches = self._apply_public_entity_evidence(v2_matches, segments)
        v2_result = DetectionResult(
            matches=v2_matches, scanned_direction=direction
        )
        decision = self._evaluator.evaluate(v2_result, profile)

        refined: list[MatchAction] = []
        for ma in decision.match_actions:
            refined.append(self._refine_numeric(self._annotate(ma, context)))
        return (
            EnforcementDecision(
                match_actions=refined,
                policy_profile_id=decision.policy_profile_id,
                reasons=decision.reasons,
            ),
            truncated,
        )

    @staticmethod
    def _apply_public_entity_evidence(matches, segments):
        """Phase 3: tag relaxable name/place/org findings that overlap a
        known public-entity span (span-scoped, same message_index).

        Only attaches ``policy_v2.public_entity`` metadata — it does NOT
        change the action here (that happens in ``_refine_numeric``), and
        it never touches a finding that isn't a relaxable name/place/org
        type. Non-destructive: the finding object is preserved (a tagged
        copy replaces it). Registry failure → no spans → no change.
        """
        spans = public_entity_registry.match_segments(segments)
        if not spans:
            return matches
        out = []
        for m in matches:
            pv = (m.metadata or {}).get(POLICY_V2_METADATA_KEY) or {}
            # Never relax a finding already classed protective.
            if pv.get("numeric_class") in (
                SECRET_NUMERIC, STRONG_IDENTIFIER, CONFIDENTIAL_NUMERIC
            ):
                out.append(m)
                continue
            mi = pv.get("message_index")
            tagged = None
            for sp in spans:
                if sp.message_index != mi:
                    continue
                # Phase 3.1: STRICT CONTAINMENT (public span ⊇ finding),
                # not any-overlap. A finding that merely overlaps a public
                # span — e.g. a PERSON finding covering "title + private
                # suffix" — must NOT be relaxed, or the private remainder
                # leaks. Only relax when the finding span lies entirely
                # within the public span.
                if not _public_span_safely_contains_finding(sp, m):
                    continue
                if not finding_relaxable_by(sp.entity_type, m.detector_name, m.entity_type):
                    continue
                meta = dict(m.metadata or {})
                npv = dict(meta.get(POLICY_V2_METADATA_KEY) or {})
                npv["public_entity"] = {
                    "entity_id": sp.entity_id,
                    "entity_type": sp.entity_type.value,
                    "rule_id": sp.rule_id,
                    "match_kind": sp.alias_type,
                }
                meta[POLICY_V2_METADATA_KEY] = npv
                tagged = replace(m, metadata=meta)
                break
            out.append(tagged if tagged is not None else m)
        return out

    @staticmethod
    def _refine_numeric(match_action: MatchAction) -> MatchAction:
        """Numeric-aware, V2-only action refinement (most protective wins).

        Public-entity relaxation (Phase 3) is applied FIRST, but only to a
        finding that is NOT secret/strong/confidential — it overrides a
        general name/place/org MASK down to ALLOW for the matched public
        span. Then numeric refinement: SECRET_NUMERIC → REDACT;
        STRONG_IDENTIFIER / CONFIDENTIAL_NUMERIC → at least MASK (via the
        explicit ``most_protective_action`` ranking).
        """
        pv = (match_action.match.metadata or {}).get(POLICY_V2_METADATA_KEY) or {}
        nclass = pv.get("numeric_class")

        # Phase 3 — public entity span relaxation (downward to ALLOW).
        if pv.get("public_entity") is not None and nclass not in (
            SECRET_NUMERIC, STRONG_IDENTIFIER, CONFIDENTIAL_NUMERIC
        ):
            if match_action.action == EnforcementAction.ALLOW:
                return match_action
            return replace(
                match_action,
                action=EnforcementAction.ALLOW,
                reason_code="public_entity_relax",
                reason_message="Span matched a verified public entity; relaxed.",
            )

        if nclass is None:
            return match_action
        if nclass == SECRET_NUMERIC:
            target = EnforcementAction.REDACT
        elif nclass in (STRONG_IDENTIFIER, CONFIDENTIAL_NUMERIC):
            target = EnforcementAction.MASK
        else:
            return match_action
        new_action = most_protective_action([match_action.action, target])
        if new_action == match_action.action:
            return match_action
        return replace(
            match_action,
            action=new_action,
            reason_code=f"numeric_refine_{nclass.lower()}",
            reason_message=f"Numeric class {nclass} enforced to {new_action.name}.",
        )

    @staticmethod
    def _annotate(
        match_action: MatchAction, context: DLPRequestContext
    ) -> MatchAction:
        """Merge the ``policy_v2`` metadata namespace onto a match.

        Preserves any keys the NumericContextAnalyzer already set
        (numeric_class / evidence / rule_id / decision_reason) and fills
        in request context. Never overwrites an analyzer-provided rule_id.
        """
        match = match_action.match
        existing = dict(match.metadata or {})
        pv = dict(existing.get(POLICY_V2_METADATA_KEY) or {})
        pv.setdefault("rule_id", match_action.reason_code)
        pv.setdefault("decision_reason", match_action.reason_message)
        pv["source_type"] = context.source_type.value
        pv["channel"] = context.channel
        pv["direction"] = context.direction.value
        existing[POLICY_V2_METADATA_KEY] = pv
        new_match = replace(match, metadata=existing)
        return replace(match_action, match=new_match)

    def _shadow_compare(
        self,
        legacy: EnforcementDecision,
        v2: EnforcementDecision,
        context: DLPRequestContext,
        *,
        shadow: bool,
    ) -> int:
        """Compare legacy vs V2 decisions; log sanitized divergences.

        Counts three kinds of difference (Phase 2A numeric work routinely
        adds supplemental findings or suppresses weak phones, so add/remove
        must be counted too, not just same-span action changes):
          - changed: same finding, different action;
          - v2_added: a finding only V2 produced (supplemental numeric);
          - v2_suppressed: a finding only legacy produced (e.g. weak phone).
        Pairs by ``(detector_name, start, end)``. Never logs matched_text
        or numeric values — only class / action / span length.
        """
        legacy_map = {
            (ma.match.detector_name, ma.match.start, ma.match.end): ma
            for ma in legacy.match_actions
        }
        v2_map = {
            (ma.match.detector_name, ma.match.start, ma.match.end): ma
            for ma in v2.match_actions
        }

        def _nclass(ma):
            return (ma.match.metadata or {}).get(
                POLICY_V2_METADATA_KEY, {}
            ).get("numeric_class")

        changed = added = suppressed = 0
        for key, v2_ma in v2_map.items():
            legacy_ma = legacy_map.get(key)
            if legacy_ma is None:
                added += 1
                logger.warning(
                    "dlp_policy_v2_shadow_added",
                    detector_name=v2_ma.match.detector_name,
                    category=v2_ma.match.category,
                    numeric_class=_nclass(v2_ma),
                    v2_action=v2_ma.action.name.lower(),
                    span_length=v2_ma.match.end - v2_ma.match.start,
                    **context.safe_log_dict(),
                )
            elif legacy_ma.action != v2_ma.action:
                changed += 1
                logger.warning(
                    "dlp_policy_v2_shadow_divergence",
                    detector_name=v2_ma.match.detector_name,
                    category=v2_ma.match.category,
                    numeric_class=_nclass(v2_ma),
                    legacy_action=legacy_ma.action.name.lower(),
                    v2_action=v2_ma.action.name.lower(),
                    span_length=v2_ma.match.end - v2_ma.match.start,
                    **context.safe_log_dict(),
                )
        for key, legacy_ma in legacy_map.items():
            if key not in v2_map:
                suppressed += 1
                logger.warning(
                    "dlp_policy_v2_shadow_suppressed",
                    detector_name=legacy_ma.match.detector_name,
                    category=legacy_ma.match.category,
                    legacy_action=legacy_ma.action.name.lower(),
                    span_length=legacy_ma.match.end - legacy_ma.match.start,
                    **context.safe_log_dict(),
                )
        divergences = changed + added + suppressed
        if shadow:
            logger.info(
                "dlp_policy_v2_shadow",
                legacy_overall=legacy.overall_action.name.lower(),
                v2_overall=v2.overall_action.name.lower(),
                v2_match_count=len(v2.match_actions),
                changed=changed,
                v2_added=added,
                v2_suppressed=suppressed,
                divergences=divergences,
                **context.safe_log_dict(),
            )
        return divergences

    @staticmethod
    def _fail_safe_decision(
        findings: list[DetectionMatch], profile: PolicyProfile
    ) -> EnforcementDecision:
        """Deterministic fail-closed decision used when evaluation throws.

        Per the Phase 1 failure policy:
        - secret / credential findings → BLOCK (fail closed),
        - every other finding → MASK,
        - NEVER ALLOW a detected finding (no raw pass-through).

        With zero findings the decision is empty → overall ALLOW, but no
        sensitive content was detected so nothing leaks.
        """
        match_actions: list[MatchAction] = []
        for match in findings:
            if match.category in _FAIL_CLOSED_CATEGORIES:
                action = EnforcementAction.BLOCK
            else:
                action = EnforcementAction.MASK
            match_actions.append(
                MatchAction(
                    match=match,
                    action=action,
                    reason_code="policy_engine_failsafe",
                    reason_message=(
                        "Policy engine failed; applied deterministic "
                        "fail-closed action."
                    ),
                )
            )
        return EnforcementDecision(
            match_actions=match_actions,
            policy_profile_id=getattr(profile, "profile_id", "default"),
            reasons=["Policy engine fail-safe decision."],
        )
