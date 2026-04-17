"""DLP domain contracts for the Secure AI Gateway.

These are the data structures passed between DLP pipeline stages:

    Detector  ──►  DetectionMatch
    Scanner   ──►  DetectionResult   (collection of matches)
    Evaluator ──►  MatchAction        (per-match decision)
              ──►  EnforcementDecision (overall decision)
    Transformer ─► TransformationResult
    Audit     ──►  DLPAuditEntry      (privacy-safe, no raw PII)

Design constraints:
- Framework-agnostic: only stdlib imports (dataclasses, enum, typing).
- No business logic — these are data carriers.
- DetectionMatch.matched_text is transient memory only; DLPAuditEntry
  intentionally excludes it to prevent PII from reaching persistence.
- SensitivityLevel and EnforcementAction are ordered enums so that
  "highest severity" and "escalation" logic can use max().
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SensitivityLevel(enum.IntEnum):
    """Ordered severity of a detection match.

    IntEnum so that comparison / max() works naturally:
        SensitivityLevel.LOW < SensitivityLevel.CRITICAL  →  True
    """

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class EnforcementAction(enum.IntEnum):
    """Ordered policy action.  Higher value = more restrictive.

    Escalation logic: overall action = max(per-match actions).
    """

    ALLOW = 0
    MASK = 1
    REDACT = 2
    BLOCK = 3


# ---------------------------------------------------------------------------
# Detection contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectionMatch:
    """A single sensitive-data finding produced by a detector.

    Attributes
    ----------
    detector_name : str
        Which detector produced this match (e.g. "email", "credit_card").
    entity_type : str
        Semantic type label (e.g. "email_address", "credit_card_number").
    category : str
        Broad classification bucket ("pii", "financial", "secret").
    start : int
        Character offset where the match begins (inclusive).
    end : int
        Character offset where the match ends (exclusive).
    matched_text : str
        The raw matched value.  Held in transient memory ONLY —
        never persisted to logs, audit, or database.
    severity : SensitivityLevel
        How sensitive this finding is.
    confidence : float
        Detector confidence, 0.0–1.0.  Deterministic regex detectors
        emit 1.0; future ML detectors may emit lower values.
    metadata : dict | None
        Optional extensibility slot for detector-specific data
        (e.g. ML model name, entity subtype, match context).
    """

    detector_name: str
    entity_type: str
    category: str
    start: int
    end: int
    matched_text: str
    severity: SensitivityLevel
    confidence: float = 1.0
    metadata: dict[str, Any] | None = None
    # Specificity hint used by the transformer's overlap resolver.
    # Higher values beat lower ones when two detectors match the
    # same span with the SAME enforcement action (e.g. the specific
    # ``thai_national_id`` detector winning over the generic ``phone``
    # detector on a 13-digit run). Never overrides a more restrictive
    # action — ``BLOCK`` always beats ``MASK`` regardless of priority.
    # Detectors that don't care leave it at 0.
    priority: int = 0


@dataclass
class DetectionResult:
    """Aggregated output of scanning one or more text fields.

    Attributes
    ----------
    matches : list[DetectionMatch]
        All matches found, sorted by start offset.
    scanned_direction : str
        "input" (request messages) or "output" (provider response).
    scan_target : str
        What was scanned (e.g. "messages", "response_content").
    """

    matches: list[DetectionMatch] = field(default_factory=list)
    scanned_direction: str = "input"
    scan_target: str = "messages"

    @property
    def has_sensitive_content(self) -> bool:
        return len(self.matches) > 0

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def highest_severity(self) -> SensitivityLevel:
        if not self.matches:
            return SensitivityLevel.NONE
        return max(m.severity for m in self.matches)

    @property
    def categories_found(self) -> set[str]:
        return {m.category for m in self.matches}

    @property
    def summary(self) -> dict[str, int]:
        """Count of matches per entity_type."""
        counts: dict[str, int] = {}
        for m in self.matches:
            counts[m.entity_type] = counts.get(m.entity_type, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Enforcement contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchAction:
    """A detection match paired with the policy-decided action.

    Attributes
    ----------
    match : DetectionMatch
        The original detection finding.
    action : EnforcementAction
        What to do: ALLOW, MASK, REDACT, or BLOCK.
    reason_code : str
        Machine-readable reason (e.g. "pii_mask_policy", "financial_block").
    reason_message : str
        Human-readable explanation.
    """

    match: DetectionMatch
    action: EnforcementAction
    reason_code: str
    reason_message: str


@dataclass
class EnforcementDecision:
    """Overall enforcement decision for a scanned payload.

    Attributes
    ----------
    overall_action : EnforcementAction
        The highest (most restrictive) action across all match actions.
        Computed from match_actions if not set explicitly.
    match_actions : list[MatchAction]
        Per-match decisions.
    policy_profile_id : str
        Which policy profile produced this decision.
    reasons : list[str]
        Aggregated human-readable reasons.
    """

    match_actions: list[MatchAction] = field(default_factory=list)
    policy_profile_id: str = "default"
    reasons: list[str] = field(default_factory=list)

    @property
    def overall_action(self) -> EnforcementAction:
        if not self.match_actions:
            return EnforcementAction.ALLOW
        return max(ma.action for ma in self.match_actions)

    @property
    def is_blocked(self) -> bool:
        return self.overall_action == EnforcementAction.BLOCK

    @property
    def requires_transformation(self) -> bool:
        return self.overall_action in (EnforcementAction.MASK, EnforcementAction.REDACT)


# ---------------------------------------------------------------------------
# Transformation contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransformedSpan:
    """Record of a single span replacement performed by the transformer."""

    start: int
    end: int
    original_length: int
    replacement_length: int
    action: EnforcementAction


@dataclass
class TransformationResult:
    """Output of applying transformations to a text.

    Attributes
    ----------
    transformed_text : str
        The text after all mask/redact replacements.
    transformed_spans : list[TransformedSpan]
        Record of each replacement applied.
    transformations_applied : int
        Count of spans that were actually modified (excludes ALLOW).
    """

    transformed_text: str
    transformed_spans: list[TransformedSpan] = field(default_factory=list)

    @property
    def transformations_applied(self) -> int:
        return len(self.transformed_spans)


# ---------------------------------------------------------------------------
# Audit contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DLPAuditEntry:
    """Privacy-safe audit record.  Contains NO raw matched text.

    This is the ONLY DLP type that should be persisted to logs, databases,
    or external audit systems.  The omission of matched_text is intentional
    and load-bearing — it prevents PII from reaching persistence.

    Attributes
    ----------
    detector_name : str
        Which detector found this.
    entity_type : str
        Semantic type label.
    category : str
        Broad classification bucket.
    severity : str
        Sensitivity level as string (for serialization).
    action : str
        What was done: "allow", "mask", "redact", "block".
    field_path : str
        Where in the payload this was found (e.g. "messages[2].content").
    direction : str
        "input" or "output".
    """

    detector_name: str
    entity_type: str
    category: str
    severity: str
    action: str
    field_path: str
    direction: str = "input"
