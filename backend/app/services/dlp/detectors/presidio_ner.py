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


# Tokens NER routinely mis-tags as named entities (PERSON / NRP /
# ORGANIZATION) but which are almost always section labels or
# nationality adjectives in our prompts. Filter applies to any
# entity type the analyzer emits, since the same word can be tagged
# differently depending on context (``Thai`` → NRP in "Thai ID:",
# PERSON in "I met Thai yesterday"). Comparison is
# case-insensitive against ``matched_text.strip()``.
#
# Keep this list tight — broad denylists risk suppressing legitimate
# names. Add entries only when a production trace shows a recurring
# false positive on a common label / adjective.
_NER_FALSE_POSITIVE_DENYLIST = frozenset({
    # Nationality adjective (NRP) — appears as "Thai ID:".
    "thai",
    # Field-label noun (PERSON) — surfaced once the
    # health_condition over-match stopped swallowing the line.
    "salary",
})


# AI model / vendor names that Presidio routinely flags as PERSON or
# ORGANIZATION when they appear in a model's self-introduction
# ("I am based on OpenAI's GPT-4 architecture"). Masking these turns
# downstream answers into nonsense (``based on OpenAI's [NAME_0]
# architecture``) while protecting no user data. Comparison is
# case-insensitive against ``matched_text.strip()``.
_NER_AI_MODEL_NAMES = frozenset({
    "gpt", "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-3.5", "gpt-3.5-turbo",
    "gpt-4-turbo", "o1", "o3",
    "claude", "claude-3", "claude-3.5", "claude-sonnet", "claude-opus",
    "claude-haiku",
    "gemini", "gemini-pro", "gemini-flash",
    "llama", "llama-3", "llama-3.1", "llama-3.3",
    "mistral", "mixtral", "codestral",
    "grok",
    "deepseek",
    "qwen",
    "kimi", "moonshot",
    "minimax",
    "openai", "anthropic", "google", "meta", "xai", "cohere",
    "perplexity", "openrouter",
})


# Conversational Thai particles, verbs, and politeness markers that
# Presidio routinely mis-tags as PERSON (false positives that sit in
# the 0.60-0.85 confidence band, plus a few that survive even after
# the 0.85 floor). The ``thai_name`` regex detector handles real
# Thai names (title prefix + first + last), so dropping these from
# the NER stream protects no data and stops the model from inventing
# ``[NAME_N]`` substitutions for everyday conversation. Compared
# case-insensitively against ``matched_text.strip()``.
_THAI_COMMON_WORDS = frozenset({
    "ตอบ", "ตอบสั้น", "ตอบสั้นๆ", "สรุป", "สรุปข้อมูล",
    "ช่วย", "ช่วยสรุป", "ข้อมูล", "ให้หน่อย", "หน่อย",
    "สวัสดี", "ครับ", "ค่ะ", "นะ", "นะครับ", "นะคะ",
    "ได้เลย", "ได้เลยครับ", "บ้าง", "อะไร",
    "มีอะไร", "ยังไง",
})


# Letter-number-letter codes that NER mis-tags as PERSON.
# Real names don't contain hyphenated digit groups, so a strict
# structural match catches product / inventory / order codes
# (``ABC-1234-TH``, ``XX-99-EN``) without touching legitimate
# people's names. Pattern is intentionally narrow.
import re as _re  # local alias — keep the top-of-file imports tidy
_PRODUCT_CODE_RE = _re.compile(r"^[A-Z]{1,5}-\d{1,6}-[A-Z]{1,5}$")


# Unicode range for the modern Thai script (basic Thai block,
# U+0E00–U+0E7F). Escape sequences keep the source file safe to
# diff in editors that don't render Thai glyphs.
_THAI_MIN = "฀"
_THAI_MAX = "๿"


def _thai_char_count(text: str) -> int:
    return sum(1 for ch in text if _THAI_MIN <= ch <= _THAI_MAX)


def _is_ner_false_positive(
    matched_text: str, category: str, confidence: float
) -> bool:
    """Return True if a NER match should be silently dropped.

    Filters (all OR-ed):

    1. The match's text (case-insensitive, stripped) appears in the
       hand-curated denylist of recurring label / adjective false
       positives.
    2. The match's text is a known AI model / vendor name. Real users
       almost never reference these as PII; the model echoing them
       back triggered ``[NAME_0]`` substitutions that broke responses.
    3. The match's text is a common Thai conversational particle /
       verb that the English NER mis-tags as PERSON. The dedicated
       ``thai_name`` regex detector catches real Thai names — this
       filter just suppresses the noise spaCy emits on everyday
       Thai prose.
    4. The match ends in ``ๆ`` (Thai reduplication marker) — never
       part of a person's name; always means "repeat the prior word".
    5. A Thai-script match with ``<= 2`` Thai characters. Real Thai
       given names start at 3 characters once vowels and tone marks
       are counted, so anything shorter is almost certainly a
       function word the NER mis-tagged.
    6. Very short matches (``<= 3`` chars) tagged as ``pii``. Presidio
       over-confidently tags 2-3 letter acronyms (``GPT``, ``API``)
       as PERSON.
    7. Short matches (``<= 4`` chars) with sub-0.9 confidence. Catches
       the borderline single-word PERSON hits from spaCy that the
       0.85 floor lets through but which are almost always noise.
    """
    text = matched_text.strip().lower()
    if not text:
        return True
    if text in _NER_FALSE_POSITIVE_DENYLIST:
        return True
    if text in _NER_AI_MODEL_NAMES:
        return True
    if text in _THAI_COMMON_WORDS:
        return True
    if text.endswith("ๆ"):
        return True
    # Product / inventory code structural filter — compares against
    # ``matched_text`` (case-preserved) because product codes are
    # conventionally uppercase, e.g. ``ABC-1234-TH``.
    if _PRODUCT_CODE_RE.match(matched_text.strip()):
        return True
    thai_chars = _thai_char_count(text)
    if thai_chars > 0 and thai_chars <= 2:
        return True
    if len(text) <= 3 and category == "pii":
        return True
    if len(text) <= 4 and confidence < 0.9:
        return True
    return False


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
    # cluster tightly around 0.85 for spaCy-backed recognizers and
    # 0.6-0.8 for the borderline hits that turn out to be common
    # Thai verbs / particles ("ตอบสั้นๆ", "ช่วยสรุป") spaCy mis-tags
    # as PERSON. Raising the floor to 0.85 keeps the strong real-name
    # hits and drops the noise band — verified end-to-end on the
    # Thai conversational corpus.
    SCORE_THRESHOLD = 0.85

    def detect(
        self, text: str, language: str = "en"
    ) -> list[DetectionMatch]:
        # Prefer the dedicated NER microservice when configured. The
        # service hosts the spaCy model once per pod (vs once per
        # worker) and returns identically-shaped findings, so the rest
        # of this method's contract is unchanged. When the service URL
        # is empty we fall back to the legacy in-process Presidio
        # analyzer — keeps dev machines without docker-compose working.
        from app.core.config import settings

        if settings.ner_service_url:
            return self._detect_via_service(text, language=language)

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
            score = float(getattr(r, "score", 1.0))
            if score < self.SCORE_THRESHOLD:
                continue
            matched_text = text[r.start:r.end]
            # Drop label / nationality adjectives the analyzer mis-tags,
            # AI model / vendor names, common Thai conversational words,
            # ``ๆ``-suffixed reduplications, and short / low-confidence
            # acronyms — see ``_is_ner_false_positive`` for the rationale.
            if _is_ner_false_positive(matched_text, self.category, score):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=r.entity_type.lower(),
                    category=self.category,
                    start=r.start,
                    end=r.end,
                    matched_text=matched_text,
                    severity=self.default_sensitivity,
                    confidence=score,
                )
            )
        return matches

    def _detect_via_service(
        self, text: str, *, language: str
    ) -> list[DetectionMatch]:
        """Call the NER microservice and convert its entities to matches.

        Failure path is enforced by ``ner_service_client.analyze_sync``
        — any timeout / 5xx / network error returns ``[]`` so the
        detector silently degrades to "no NER findings" instead of
        breaking the DLP pipeline.
        """
        from app.services.ner_service_client import analyze_sync

        entities = analyze_sync(
            text,
            language=language,
            entities=list(self.ENTITIES),
            score_threshold=self.SCORE_THRESHOLD,
        )
        if not entities:
            return []

        matches: list[DetectionMatch] = []
        for e in entities:
            if e.score < self.SCORE_THRESHOLD:
                continue
            matched_text = text[e.start : e.end]
            if _is_ner_false_positive(matched_text, self.category, e.score):
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=e.label.lower(),
                    category=self.category,
                    start=e.start,
                    end=e.end,
                    matched_text=matched_text,
                    severity=self.default_sensitivity,
                    confidence=e.score,
                )
            )
        return matches
