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

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


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

        try:
            results = analyzer.analyze(
                text=text,
                language=language,
                entities=list(self.ENTITIES),
                score_threshold=self.SCORE_THRESHOLD,
            )
        except Exception:
            # Fail-open: never propagate analyzer failures into the
            # DLP pipeline. A broken analyzer must not take down the
            # gateway.
            return []

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
