"""Pure matching primitives for custom detectors (Sprint 6, shared by B + E).

Synchronous, stateless functions that turn a stored ``pattern`` dict + text
into ``(start, end, matched_text)`` spans. Used by:

  * ``CustomDetectorService`` — to evaluate inline test cases at save time.
  * the runtime executor (Batch E) — wrapped in a ``BaseDetector`` subclass
    that runs on the scan hot path.

Regex matching uses the third-party ``regex`` module with a per-call
``timeout=`` so a single match attempt can't exceed the 50ms hard cap; a
``TimeoutError`` propagates to the caller (the executor records it and may
auto-disable the detector). Semantic (Presidio) matching is NOT done here —
it's delegated to the existing NER detector at runtime — so this module
covers ``regex`` and ``keyword_list`` only.
"""

from __future__ import annotations

import regex

# Default per-attempt timeout (seconds) — the 50ms hard cap.
MATCH_TIMEOUT_SEC = 0.05

_Span = tuple[int, int, str]


def match_regex(
    pattern_src: str,
    flags: str,
    text: str,
    timeout_sec: float = MATCH_TIMEOUT_SEC,
) -> list[_Span]:
    """Return all non-overlapping match spans. May raise ``TimeoutError``."""
    if not pattern_src or not text:
        return []
    compile_flags = regex.IGNORECASE if "i" in (flags or "") else 0
    compiled = regex.compile(pattern_src, compile_flags)
    spans: list[_Span] = []
    for m in compiled.finditer(text, timeout=timeout_sec):
        spans.append((m.start(), m.end(), m.group(0)))
    return spans


def match_keywords(
    keywords: list[str], case_sensitive: bool, text: str
) -> list[_Span]:
    """Return spans for each keyword occurrence (literal substring match)."""
    if not keywords or not text:
        return []
    hay = text if case_sensitive else text.lower()
    spans: list[_Span] = []
    for kw in keywords:
        if not kw:
            continue
        needle = kw if case_sensitive else kw.lower()
        start = 0
        while True:
            idx = hay.find(needle, start)
            if idx == -1:
                break
            spans.append((idx, idx + len(kw), text[idx : idx + len(kw)]))
            start = idx + len(kw)
    return spans


def run_matches(
    detection_type: str,
    pattern: dict,
    text: str,
    timeout_sec: float = MATCH_TIMEOUT_SEC,
) -> list[_Span]:
    """Dispatch matching by detector type. Semantic returns [] (delegated)."""
    pattern = pattern or {}
    if detection_type == "regex":
        return match_regex(
            str(pattern.get("regex", "")),
            str(pattern.get("flags", "") or ""),
            text,
            timeout_sec,
        )
    if detection_type == "keyword_list":
        return match_keywords(
            list(pattern.get("keywords", []) or []),
            bool(pattern.get("case_sensitive", False)),
            text,
        )
    # semantic — handled by the runtime Presidio path, not here.
    return []
