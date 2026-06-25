"""Safety validation for admin-supplied DLP detector patterns (Sprint 6, C).

Every user-supplied pattern MUST pass this pre-flight before it can be saved
or run on the scan hot path. The hard requirements (see sprint safety spec):

  1. ReDoS protection — reject obvious catastrophic-backtracking shapes AND
     prove the compiled pattern completes on a pathological input within a
     50ms hard cap (enforced by the ``regex`` module's own ``timeout=``).
  2. Length / size caps so a single detector can't blow up compile time.

Uses the third-party ``regex`` module (NOT stdlib ``re``) because only
``regex`` supports a per-call ``timeout=`` — the same mechanism the runtime
executor uses for the 50ms cap. ``re`` is used only for the cheap static
ReDoS-shape heuristic (it never runs user input).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

import regex

# --- Tunables ---------------------------------------------------------------

_MAX_PATTERN_CHARS = 500
_MAX_COMPILE_MS = 50.0
_MATCH_TIMEOUT_SEC = 0.05  # 50ms hard cap on the pathological match
_MAX_KEYWORDS = 100
_MAX_KEYWORD_CHARS = 200

# Static heuristics for catastrophic-backtracking shapes. These never execute
# user input — they pattern-match the regex SOURCE.
_REDOS_SHAPES = [
    r"\([^)]*[+*][^)]*\)[+*]",   # nested quantifier: (a+)+ / (a*)*
    r"\([^)]*\|[^)]*\)[+*]",      # alternation under quantifier: (a|a)+
    r"\([^)]*\?[^)]*\)\?",        # optional-of-optional: (a?)?
]

# Pathological input designed to expose ReDoS (long run + a failing tail).
_REDOS_TEST_INPUT = "a" * 1000 + "!"

# Presidio-supported semantic entity types (Sprint 6 ships no new spaCy
# models, so the allowed set is what the analyzer already recognises).
_SEMANTIC_ENTITIES = {
    "PERSON", "LOCATION", "ORGANIZATION", "EMAIL_ADDRESS",
    "PHONE_NUMBER", "URL", "IP_ADDRESS", "DATE_TIME",
    "CREDIT_CARD", "US_SSN", "IBAN_CODE", "CRYPTO",
}


@dataclass
class ValidationResult:
    valid: bool
    error: Optional[str] = None
    compile_time_ms: Optional[float] = None
    test_match_time_ms: Optional[float] = None


def validate_regex(pattern: str, flags: str = "") -> ValidationResult:
    """Pre-flight a regex: length, syntax, ReDoS shape, and timed match."""
    if not pattern:
        return ValidationResult(valid=False, error="empty pattern")
    if len(pattern) > _MAX_PATTERN_CHARS:
        return ValidationResult(
            valid=False, error=f"pattern too long (>{_MAX_PATTERN_CHARS} chars)"
        )

    # Static ReDoS-shape heuristic (cheap, before we compile anything).
    for danger in _REDOS_SHAPES:
        if re.search(danger, pattern):
            return ValidationResult(
                valid=False,
                error="potential ReDoS: nested quantifier or alternation "
                "under a quantifier detected",
            )

    # Syntax + compile-time check.
    compile_flags = regex.IGNORECASE if "i" in flags else 0
    try:
        t0 = time.monotonic()
        compiled = regex.compile(pattern, compile_flags)
        compile_ms = (time.monotonic() - t0) * 1000
    except regex.error as exc:
        return ValidationResult(valid=False, error=f"syntax error: {exc}")

    if compile_ms > _MAX_COMPILE_MS:
        return ValidationResult(
            valid=False, error=f"compile too slow (>{_MAX_COMPILE_MS:.0f}ms)"
        )

    # Timed match against pathological input — the regex module raises
    # TimeoutError if it exceeds the hard cap.
    try:
        t1 = time.monotonic()
        compiled.findall(_REDOS_TEST_INPUT, timeout=_MATCH_TIMEOUT_SEC)
        match_ms = (time.monotonic() - t1) * 1000
    except TimeoutError:
        return ValidationResult(
            valid=False,
            error="regex too slow on pathological input (>50ms) — likely ReDoS",
        )

    return ValidationResult(
        valid=True, compile_time_ms=compile_ms, test_match_time_ms=match_ms
    )


def validate_keyword_list(keywords: list[str]) -> ValidationResult:
    """Validate a keyword-list detector's terms."""
    if not keywords:
        return ValidationResult(valid=False, error="empty keyword list")
    if len(keywords) > _MAX_KEYWORDS:
        return ValidationResult(
            valid=False, error=f"too many keywords (>{_MAX_KEYWORDS})"
        )
    for kw in keywords:
        if not isinstance(kw, str) or len(kw.strip()) == 0:
            return ValidationResult(valid=False, error="empty keyword in list")
        if len(kw) > _MAX_KEYWORD_CHARS:
            return ValidationResult(
                valid=False, error=f"keyword too long: {kw[:50]}..."
            )
    return ValidationResult(valid=True)


def validate_semantic_entity(entity_type: str) -> ValidationResult:
    """Validate a semantic detector's entity type against Presidio's set."""
    if entity_type not in _SEMANTIC_ENTITIES:
        return ValidationResult(
            valid=False,
            error="unsupported entity type. Use one of: "
            + ", ".join(sorted(_SEMANTIC_ENTITIES)),
        )
    return ValidationResult(valid=True)


def validate_pattern(detection_type: str, pattern: dict) -> ValidationResult:
    """Dispatch validation by detector type over the stored ``pattern`` dict.

    Pattern shapes (the canonical contract used by the service + executor):
      * regex        → {"regex": "<src>", "flags": "i"}
      * keyword_list → {"keywords": [...], "case_sensitive": bool}
      * semantic     → {"entity_type": "PERSON"}
    """
    pattern = pattern or {}
    if detection_type == "regex":
        return validate_regex(
            str(pattern.get("regex", "")), str(pattern.get("flags", "") or "")
        )
    if detection_type == "keyword_list":
        kws = pattern.get("keywords")
        if not isinstance(kws, list):
            return ValidationResult(valid=False, error="keywords must be a list")
        return validate_keyword_list(kws)
    if detection_type == "semantic":
        return validate_semantic_entity(str(pattern.get("entity_type", "")))
    return ValidationResult(
        valid=False, error=f"unknown detection_type: {detection_type}"
    )
