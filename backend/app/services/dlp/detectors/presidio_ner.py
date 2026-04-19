"""Presidio-backed NER detector.

Wraps a shared ``AnalyzerEngine`` (owned by ``ner_status``) to find
entity-typed spans that regex detectors can't: PERSON, LOCATION,
ORGANIZATION, GPE, NRP. Emitted as ``category="pii"`` so existing
policy profiles that mask ``pii`` also mask NER findings without
needing per-category config.

Lifecycle / safety:

- Module is import-safe even when ``presidio-analyzer`` is not
  installed. The analyzer import lives inside ``ner_status`` and is
  only reached after ``load_analyzer_sync`` runs successfully.
- ``detect()`` returns ``[]`` when the analyzer is not loaded, so
  registering this detector is always safe — the registry calls it
  unconditionally and the per-tenant / per-org gate in the
  orchestrator handles whether it runs at all.
- ``detect()`` never raises. Any exception from the analyzer is
  swallowed and reported as "no matches" so a Presidio hiccup cannot
  break the request pipeline.
"""

from __future__ import annotations

import structlog

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


logger = structlog.get_logger(__name__)

# Startup health check: without at least one spaCy model the analyzer
# loads successfully but produces zero matches — the detector silently
# no-ops and any NER-dependent finding slips past the gateway. Run this
# at module import so the warning lands in the boot log instead of only
# showing up after a surprised operator inspects a leaked request.
_CANDIDATE_SPACY_MODELS = (
    "en_core_web_lg",
    "en_core_web_sm",
    "th_core_news_lg",
)


def _check_spacy_models() -> None:
    try:
        import spacy  # noqa: WPS433 — intentional lazy import
    except Exception:
        # presidio-analyzer pulls spaCy in; if it's missing the whole
        # detector is inert anyway, which the registry gate handles.
        return
    available: list[str] = []
    missing: list[str] = []
    for name in _CANDIDATE_SPACY_MODELS:
        try:
            spacy.util.get_package_path(name)  # fast path — no full load
            available.append(name)
        except Exception:
            missing.append(name)
    if not available:
        logger.warning(
            "dlp_presidio_no_spacy_model",
            missing=missing,
            hint=(
                "No spaCy model found in the container. "
                "PresidioDetector will return [] for every request. "
                "Install one with e.g. "
                "`python -m spacy download en_core_web_sm`."
            ),
        )
    elif "en_core_web_lg" not in available:
        logger.info(
            "dlp_presidio_using_fallback_spacy_model",
            available=available,
            note=(
                "en_core_web_lg is missing; PresidioDetector will use a "
                "smaller fallback model. Accuracy may be lower than the "
                "large model."
            ),
        )


_check_spacy_models()


class PresidioDetector(BaseDetector):
    name = "presidio_ner"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    # Registry passes the request language to detectors whose class sets
    # NEEDS_LANGUAGE=True, and calls detect() without kwargs for others.
    # Keeps the BaseDetector contract unchanged for the 13 regex
    # detectors — only this one sees the language kwarg.
    NEEDS_LANGUAGE = True

    # Presidio entity labels we care about. NRP = nationalities,
    # religious or political groups. GPE = geo-political entity
    # (country / city / state).
    ENTITIES = ("PERSON", "LOCATION", "ORGANIZATION", "GPE", "NRP")

    # Minimum confidence for a result to be recorded. Presidio scores
    # cluster tightly around 0.85 for spaCy-backed recognizers, so 0.7
    # filters out weak / ambiguous hits without losing obvious names.
    SCORE_THRESHOLD = 0.7

    def detect(
        self, text: str, language: str = "en"
    ) -> list[DetectionMatch]:
        from app.services.dlp.ner_status import get_analyzer

        analyzer = get_analyzer()
        if analyzer is None:
            return []

        # Mixed-language content (Thai prose with embedded English
        # names) gets classified as "th" by the language detector, but
        # an English-only analyzer build has no Thai recognizers and
        # returns "No matching recognizers". Fall back to "en" in that
        # case — spaCy's English NER still finds English names
        # ("John Smith") even when they're embedded in Thai text, and
        # missing those is worse than the alternative of running the
        # English recognizer on some Thai words that won't match.
        supported = set(getattr(analyzer, "supported_languages", ["en"]) or ["en"])
        languages_to_try: list[str] = []
        if language in supported:
            languages_to_try.append(language)
        if "en" in supported and "en" not in languages_to_try:
            languages_to_try.append("en")
        if not languages_to_try:
            # Exotic config — attempt the caller's language anyway; the
            # try/except below catches the failure.
            languages_to_try = [language]

        results = []
        for lang in languages_to_try:
            try:
                results = analyzer.analyze(
                    text=text,
                    language=lang,
                    entities=list(self.ENTITIES),
                    score_threshold=self.SCORE_THRESHOLD,
                )
                if results:
                    break
            except Exception:
                # Fail-open: never propagate analyzer failures into the
                # DLP pipeline. A broken analyzer must not take down
                # the gateway. Try the next fallback language if any.
                continue
        else:
            results = []

        matches: list[DetectionMatch] = []
        for r in results:
            if getattr(r, "score", 1.0) < self.SCORE_THRESHOLD:
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=r.entity_type.lower(),
                    category=self.category,
                    start=r.start,
                    end=r.end,
                    matched_text=text[r.start:r.end],
                    severity=self.default_sensitivity,
                    confidence=float(getattr(r, "score", 1.0)),
                )
            )
        return matches
