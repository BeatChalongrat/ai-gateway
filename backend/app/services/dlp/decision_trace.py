"""FIX38 B3 — the decision trace: *why* was this span masked?

Presidio keeps a ``decision_process`` on every result so an operator can
see which recognizer fired, what the raw score was, and which context
words moved it. This gateway had nothing of the kind: a false fire or a
leak in production left only ``findings=3`` in the log. This module
writes one structured line per scan that had findings, listing for each
match the Presidio-style **layer** that produced it, the confidence, the
signals the context enhancer used, and the action the policy chose.

What it deliberately does NOT contain: the matched text. The trace is
for debugging the detector, and the whole point of the detector is that
the value never lands in a log. Offsets and length are enough to find
the span in the original message when an operator has it.

Layers (one label per detector, in Presidio's vocabulary):

    pattern             a regex detector
    pattern+validator   regex + checksum (Thai ID, cards, IBAN, tax id …)
    pattern+context     regex whose emission is gated on nearby words
                        (phone's bare-digit branch, the no-prefix name pass)
    ner                 the English Presidio analyzer
    ner+context         the Thai NER layer: CRF / lexicon candidates
                        scored by the context enhancer (``signals`` says
                        exactly which terms contributed)

Reading a line: ``layer=ner+context conf=0.75 signals=base:nickname,title``
means "a nickname-lexicon hit with a title in front of it scored 0.75 and
cleared the threshold". ``layer=pattern conf=1.0`` means the regex is the
verdict — there is nothing to tune.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Detector name -> Presidio layer label. Anything not listed is ``pattern``.
_VALIDATED = frozenset({
    "thai_national_id", "credit_card", "iban", "thai_tax_id", "ssn",
    "thai_bank_account", "thai_promptpay", "passport",
})
_CONTEXT_GATED = frozenset({
    "phone", "thai_name_no_prefix", "medical_record", "income",
    "date_of_birth", "vehicle_plate",
})
_THAI_NER = frozenset({"thai_ner", "thai_ner_location"})
_PRESIDIO = frozenset({"presidio_ner"})

MAX_FINDINGS_PER_LINE = 50


def layer_of(detector_name: str) -> str:
    if detector_name in _THAI_NER:
        return "ner+context"
    if detector_name in _PRESIDIO:
        return "ner"
    if detector_name in _VALIDATED:
        return "pattern+validator"
    if detector_name in _CONTEXT_GATED:
        return "pattern+context"
    return "pattern"


def describe(match_action: Any) -> dict[str, Any]:
    """One trace entry for a ``MatchAction``. Never includes the text."""
    m = match_action.match
    meta = m.metadata or {}
    entry: dict[str, Any] = {
        "detector": m.detector_name,
        "layer": layer_of(m.detector_name),
        "entity": m.entity_type,
        "span": [m.start, m.end],
        "len": max(0, int(m.end) - int(m.start)),
        "conf": round(float(m.confidence), 3) if m.confidence is not None else None,
        "priority": m.priority,
        "action": getattr(match_action.action, "name", str(match_action.action)).lower(),
        "reason": match_action.reason_code,
    }
    if meta.get("signals"):
        entry["signals"] = meta["signals"]
    if meta.get("detected_via"):
        entry["via"] = meta["detected_via"]
    return entry


def trace_decision(
    decision: Any,
    *,
    direction: str | None,
    log: Any | None = None,
) -> None:
    """Emit the ``dlp_decision_trace`` line for one enforcement decision.

    Silent when the trace is switched off or the scan found nothing —
    the trace exists to explain findings, not to add a line to every
    clean request. Never raises: a broken trace must not touch the scan.
    """
    try:
        from app.core.config import settings

        if not getattr(settings, "dlp_decision_trace_enabled", False):
            return
        actions = list(getattr(decision, "match_actions", None) or [])
        if not actions:
            return
        entries = [describe(ma) for ma in actions[:MAX_FINDINGS_PER_LINE]]
        layers: dict[str, int] = {}
        for e in entries:
            layers[e["layer"]] = layers.get(e["layer"], 0) + 1
        (log or logger).info(
            "dlp_decision_trace",
            direction=direction,
            overall=getattr(getattr(decision, "overall_action", None), "name", "?").lower(),
            profile=getattr(decision, "policy_profile_id", None),
            findings=len(actions),
            truncated=len(actions) > MAX_FINDINGS_PER_LINE,
            layers=layers,
            trace=entries,
        )
    except Exception as exc:  # noqa: BLE001 — observability must never break enforcement
        logger.debug("dlp_decision_trace_failed", error=str(exc)[:120])
