"""FIX37 — Thai NER detector (input direction only).

The gateway had Presidio's layers 1 and 3 (pattern + validator) but not
2 and 4 (an NER model and a context enhancer). Thai personal names have
no pattern — ``แก้ว``, ``ทอง``, ``ชัย``, ``ฟ้า`` are ordinary words — so
regex alone measured 0.625 recall on names and 0.52 on addresses while
firing on nothing it shouldn't. This detector adds the missing layers.

Three candidate sources feed one scorer
(:mod:`app.services.dlp.detectors.thai_ner_scoring`):

1. the Thai NER model in the ner-service (CRF ThaiNER),
2. the nickname lexicon (``thai_nicknames.json``) — nicknames are the
   single biggest gap and no model covers them,
3. the formal given-name gazetteer already in the repo.

None of them masks on its own. Each candidate is scored from the words
around it and only spans at or above ``dlp_thai_ner_threshold`` are
emitted, which is what keeps the must-not-fire corpus at zero.

Direction: **input only.** The outbound path scans once per streamed
chunk (measured: 26 window scans for a long answer), so NER never runs
there — ``GatewayOrchestrator._select_registry(..., for_output=True)``
hands back a registry without this detector.

Failure model: ``detect`` never raises. A missing lexicon, an
unreachable ner-service, or any exception degrades to "no NER
findings" and the regex detectors carry on unchanged.
"""

from __future__ import annotations

import threading

import structlog

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors import thai_gazetteer as gaz
from app.services.dlp.detectors import thai_ner_scoring as scoring
from app.services.dlp.detectors.base import BaseDetector

logger = structlog.get_logger(__name__)

_PERSON_LABELS = {"PERSON", "PER", "PERSON_NAME"}
_LOCATION_LABELS = {"LOCATION", "LOC", "GPE", "FACILITY"}

# N6 — cheap outs before the model is ever asked.
MIN_CHARS = 8
MIN_THAI_CHARS = 3
CACHE_SIZE = 512


class _SpanCache:
    """Tiny bounded cache of (text -> spans). Repeated turns in one
    conversation re-send the same history, so the same message text is
    scanned many times per session."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._data: dict[int, list] = {}
        self._order: list[int] = []
        self._lock = threading.Lock()

    def get(self, key: int):
        with self._lock:
            return self._data.get(key)

    def put(self, key: int, value: list) -> None:
        with self._lock:
            if key in self._data:
                return
            self._data[key] = value
            self._order.append(key)
            while len(self._order) > self._size:
                self._data.pop(self._order.pop(0), None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._order.clear()


_cache = _SpanCache()


def _thai_char_count(text: str) -> int:
    return sum(1 for c in text if "ก" <= c <= "๛")


class ThaiNerDetector(BaseDetector):
    name = "thai_ner"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM
    NEEDS_LANGUAGE = True

    # Below ``thai_name`` (title-gated regex, high precision) so the
    # overlap resolver keeps the regex span for a person both engines
    # found — one human keeps one placeholder. Above
    # ``thai_name_no_prefix`` (-5), which is the weakest signal.
    priority = -3

    def detect(self, text: str, language: str = "th") -> list[DetectionMatch]:
        try:
            return self._detect(text, language)
        except Exception as exc:  # noqa: BLE001 — never break the pipeline
            logger.warning("thai_ner_failed", error=str(exc)[:200])
            return []

    # ── internals ────────────────────────────────────────────────────────

    def _detect(self, text: str, language: str) -> list[DetectionMatch]:
        from app.core.config import settings

        if not getattr(settings, "dlp_thai_ner_enabled", False):
            return []
        if not text or len(text) < MIN_CHARS:
            return []
        if _thai_char_count(text) < MIN_THAI_CHARS:
            return []
        max_chars = int(getattr(settings, "dlp_thai_ner_max_chars", 4000) or 0)
        if max_chars and len(text) > max_chars:
            # Long-context turns are dominated by pasted documents; the
            # regex layer still covers them and the latency budget does
            # not stretch to a model pass over 10k characters.
            return []

        threshold = float(getattr(settings, "dlp_thai_ner_threshold", 0.70))
        key = hash(text)
        cached = _cache.get(key)
        if cached is not None:
            entities = cached
        else:
            entities = self._model_spans(text, language)
            _cache.put(key, entities)

        regex_spans = self._regex_spans(text)
        out: list[DetectionMatch] = []
        seen: set[tuple[int, int]] = set()

        for label, start, end in entities:
            up = label.upper().split("-")[-1]
            if up in _PERSON_LABELS:
                start = scoring.trim_leading_title(text, start, end)
                if start >= end:
                    continue
                for ps, pe in scoring.split_person_span(text, start, end):
                    if scoring.non_name_prefix_before(text, ps):
                        continue
                    conf, reasons = scoring.score_person(
                        text, ps, pe,
                        source="ner",
                        in_gazetteer=self._in_gazetteer(text[ps:pe]),
                        regex_agrees=self._overlaps(regex_spans, ps, pe),
                    )
                    if conf >= threshold:
                        self._emit(out, seen, text, ps, pe, "person", conf, reasons)
                continue
            if up in _LOCATION_LABELS:
                conf, reasons = scoring.score_location(
                    text, start, end,
                    regex_agrees=self._overlaps(regex_spans, start, end),
                )
                entity_type = "location"
                if conf >= threshold:
                    self._emit(out, seen, text, start, end, entity_type, conf, reasons)

        for source, start, end in self._lexicon_spans(text):
            if self._overlaps(seen, start, end):
                continue
            # A name that is ALSO an ordinary Thai word never masks on the
            # lexicon alone: the must-not-fire corpus produced exactly that
            # shape (``ประกาศ``, ``ชนิด``, ``ควร`` scored as people because
            # a person noun happened to sit in the window). It needs a
            # title, or one of the other engines pointing at the same span.
            # FIX38 Part E adds the third acceptable second signal: a word
            # boundary on the left AND a person-only predicate glued on the
            # right (``วิชัยถูกฟ้อง``). A context word anywhere in the
            # window is NOT enough — that variant fired on the corpus.
            if source == "gazetteer_ambiguous" and not (
                scoring.title_prefix_before(text, start)
                or self._overlaps(regex_spans, start, end)
                or scoring.boundary_predicate(text, start, end)
            ):
                continue
            # ``วัดชัยมงคล`` / ``หน้าฝน`` / ``อากาศฟ้าใส`` — a place or
            # season classifier immediately before the span means the
            # match is part of that compound, not a person.
            if scoring.non_name_prefix_before(text, start):
                continue
            conf, reasons = scoring.score_person(
                text, start, end,
                source=source,
                in_gazetteer=self._in_gazetteer(text[start:end]),
                regex_agrees=self._overlaps(regex_spans, start, end),
            )
            if conf >= threshold:
                self._emit(out, seen, text, start, end, "person", conf, reasons)

        out.sort(key=lambda m: (m.start, m.end))
        return out

    def _emit(self, out, seen, text, start, end, entity_type, conf, reasons) -> None:
        if (start, end) in seen:
            return
        seen.add((start, end))
        out.append(
            DetectionMatch(
                # Person and address spans carry different detector names
                # so each inherits the right mask token (and the right
                # counter) from ``transformer.MASK_TOKENS``.
                detector_name=(
                    self.name if entity_type == "person" else "thai_ner_location"
                ),
                entity_type=entity_type,
                category=self.category,
                start=start,
                end=end,
                matched_text=text[start:end],
                severity=self.default_sensitivity,
                confidence=round(conf, 3),
                priority=self.priority,
                metadata={"signals": ",".join(reasons)},
            )
        )

    def _model_spans(self, text: str, language: str) -> list[tuple[str, int, int]]:
        """Ask the ner-service for Thai entity spans. [] on any problem."""
        from app.core.config import settings

        if not settings.ner_service_url:
            return []
        from app.services.ner_service_client import analyze_sync

        entities = analyze_sync(
            text,
            language="th",
            entities=["PERSON", "LOCATION", "ORGANIZATION"],
            # Our own scorer decides; the service must not pre-filter.
            score_threshold=0.0,
        )
        spans: list[tuple[str, int, int]] = []
        for e in entities:
            if 0 <= e.start < e.end <= len(text):
                spans.append((e.label, e.start, e.end))
        return spans

    def _lexicon_spans(self, text: str) -> list[tuple[str, int, int]]:
        """Lexicon candidates: nicknames, then gazetteer names.

        Three sources, each tagged so the scorer can start them at
        different confidences: the nickname list (no model covers Thai
        nicknames), the unambiguous given-name gazetteer, and the
        ``ambiguous`` list of names that are also ordinary words — the
        one the pre-FIX37 detector could not touch at all.
        """
        spans: list[tuple[str, int, int]] = []
        for nick in scoring.nicknames():
            idx = text.find(nick)
            while idx != -1:
                end = idx + len(nick)
                if not scoring.continues_into_word(text, idx, end):
                    spans.append(("nickname", idx, end))
                idx = text.find(nick, idx + 1)
        for s0, e0 in scoring.find_gazetteer_names(text):
            spans.append(("gazetteer", s0, e0))
        for s0, e0 in scoring.find_ambiguous_names(text):
            spans.append(("gazetteer_ambiguous", s0, e0))
        # Longest candidate wins any overlap so one person keeps one span.
        spans.sort(key=lambda sp: (sp[1], -(sp[2] - sp[1])))
        kept: list[tuple[str, int, int]] = []
        for src, s0, e0 in spans:
            if any(s0 < ke and ks < e0 for _s, ks, ke in kept):
                continue
            kept.append((src, s0, e0))
        return kept

    @staticmethod
    def _in_gazetteer(span: str) -> bool:
        s = span.strip()
        if not s:
            return False
        return s in gaz.names() or s in scoring.nicknames()

    @staticmethod
    def _regex_spans(text: str) -> set[tuple[int, int]]:
        """Spans the high-precision Thai regex detectors already claim.

        Used only as an agreement signal — this detector never suppresses
        a regex finding, and the overlap resolver (which prefers higher
        ``priority``) decides which span survives to the mask.
        """
        try:
            from app.services.dlp.detectors.thai_address import ThaiAddressDetector
            from app.services.dlp.detectors.thai_name import ThaiNameDetector

            spans = set()
            for det in (ThaiNameDetector(), ThaiAddressDetector()):
                for m in det.detect(text):
                    spans.add((m.start, m.end))
            return spans
        except Exception:  # noqa: BLE001
            return set()

    @staticmethod
    def _overlaps(spans, start: int, end: int) -> bool:
        return any(s < end and start < e for s, e in spans)
