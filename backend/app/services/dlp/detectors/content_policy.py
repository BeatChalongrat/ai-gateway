"""Content-policy detectors — Sprint 4.

Three detectors under a new ``content_policy`` category, used ONLY by the
MEDIA_GEN DLP path (wired in via the media scan; NOT added to the default
registry, so chat traffic is unaffected):

    CSAMKeywordDetector      — CSAM indicators (BLOCK, CRITICAL)
    CopyrightEntityDetector  — well-known copyrighted artists/characters (HIGH)
    PublicFigureDetector     — real public figures / likeness risk (HIGH)

Matching is case-insensitive and diacritic-insensitive ("Beyoncé" ↔
"beyonce") while preserving accurate original-text offsets, and is Thai-safe
(Thai combining marks are not stripped destructively for the match result —
offsets map back to the original string). Term/entity lists are data-driven
(``../data/*.json``); CSAM indicators are base64-encoded (never plaintext).
"""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.data.csam_indicators import INDICATORS as _CSAM_INDICATORS
from app.services.dlp.detectors.base import BaseDetector

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _ascii_fold_with_map(text: str) -> tuple[str, list[int]]:
    """Return (folded, idx_map) where folded is a casefolded, diacritic-
    stripped view of *text* and idx_map[i] is the ORIGINAL index of folded
    char i. Combining marks are dropped (so "é" → "e"); every emitted folded
    char maps back to its source index so match spans stay accurate.
    """
    folded: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(text):
        for d in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(d):
                continue
            for fc in d.casefold():
                folded.append(fc)
                idx_map.append(i)
    return "".join(folded), idx_map


def _fold_term(term: str) -> str:
    folded, _ = _ascii_fold_with_map(term)
    return folded


def _is_ascii_alnum(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _find_terms(
    text: str,
    folded_text: str,
    idx_map: list[int],
    folded_terms: list[tuple[str, str]],
) -> list[tuple[int, int]]:
    """Find each folded term in folded_text; return original (start, end)
    spans. ``folded_terms`` is a list of (folded_term, _label) — label unused
    here, kept so callers can pair results. Latin terms get word-boundary
    checks; non-Latin (Thai) terms match as substrings.
    """
    spans: list[tuple[int, int]] = []
    n = len(folded_text)
    for folded_term, _label in folded_terms:
        if not folded_term:
            continue
        tlen = len(folded_term)
        boundary = _is_ascii_alnum(folded_term[0]) and _is_ascii_alnum(folded_term[-1])
        start = 0
        while True:
            p = folded_text.find(folded_term, start)
            if p < 0:
                break
            start = p + 1
            if boundary:
                before = folded_text[p - 1] if p > 0 else ""
                after = folded_text[p + tlen] if p + tlen < n else ""
                if (before and _is_ascii_alnum(before)) or (
                    after and _is_ascii_alnum(after)
                ):
                    continue
            o_start = idx_map[p]
            o_end = idx_map[p + tlen - 1] + 1
            spans.append((o_start, o_end))
    return spans


@lru_cache(maxsize=1)
def _csam_folded_terms() -> list[tuple[str, str]]:
    return [(_fold_term(t), t) for t in sorted(_CSAM_INDICATORS)]


def _load_entities(filename: str) -> list[str]:
    path = _DATA_DIR / filename
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [e["name"] for e in data.get("entries", []) if e.get("name")]


@lru_cache(maxsize=1)
def _copyright_folded_terms() -> list[tuple[str, str]]:
    return [(_fold_term(n), n) for n in _load_entities("copyright_entities.json")]


@lru_cache(maxsize=1)
def _public_figure_folded_terms() -> list[tuple[str, str]]:
    return [(_fold_term(n), n) for n in _load_entities("public_figures.json")]


class _ContentPolicyDetector(BaseDetector):
    """Shared scan body for the content-policy detectors."""

    category = "content_policy"
    entity_type = "content_policy"

    def _folded_terms(self) -> list[tuple[str, str]]:  # pragma: no cover
        raise NotImplementedError

    def detect(self, text: str) -> list[DetectionMatch]:
        if not text:
            return []
        folded_text, idx_map = _ascii_fold_with_map(text)
        out: list[DetectionMatch] = []
        for start, end in _find_terms(text, folded_text, idx_map, self._folded_terms()):
            out.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=self.entity_type,
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=text[start:end],
                    severity=self.default_sensitivity,
                )
            )
        return out


class CSAMKeywordDetector(_ContentPolicyDetector):
    name = "csam_keyword"
    entity_type = "csam"
    default_sensitivity = SensitivityLevel.CRITICAL
    priority = 100  # beat any generic overlap

    def _folded_terms(self) -> list[tuple[str, str]]:
        return _csam_folded_terms()


class CopyrightEntityDetector(_ContentPolicyDetector):
    name = "copyright_entity"
    entity_type = "copyright"
    default_sensitivity = SensitivityLevel.HIGH

    def _folded_terms(self) -> list[tuple[str, str]]:
        return _copyright_folded_terms()


class PublicFigureDetector(_ContentPolicyDetector):
    name = "public_figure"
    entity_type = "public_figure"
    default_sensitivity = SensitivityLevel.HIGH

    def _folded_terms(self) -> list[tuple[str, str]]:
        return _public_figure_folded_terms()


def content_policy_detectors() -> list[BaseDetector]:
    """The content-policy detector instances, for the MEDIA_GEN scan path
    (Batch E). Deliberately NOT registered in the default registry."""
    return [
        CSAMKeywordDetector(),
        CopyrightEntityDetector(),
        PublicFigureDetector(),
    ]
