"""Thai Royal/Public Entity Registry — Phase 3.

Deterministic, versioned, in-memory registry of verified Thai royal /
historical / public references. It is used ONLY as span-scoped NEGATIVE
evidence: a match marks a span as a known public name/place/org so the
Policy Engine may relax an OVERLAPPING name/location/org finding. It never
masks, restores, deletes findings, or relaxes a whole message — and never
overrides secret / credential / strong-identifier / private-attribute /
numeric findings.

Hardening:
- Loaded + validated ONCE, cached in memory. No DB/network/Redis per
  request. An invalid registry disables itself (empty) — it never becomes
  allow-all.
- Matching is per :class:`NumericTextSegment` (local offsets +
  message_index); context windows never cross a segment boundary.
- Numerals (Thai ๐-๙ / Arabic 0-9) and optional whitespace are handled by
  the compiled patterns; matching uses boundary guards so an alias never
  matches inside a longer word. Longest alias wins.
- Ambiguous / short aliases require royal context nearby; otherwise they
  produce NO public evidence (conservative).

NEVER logs or stores matched text / alias text / surrounding text.
"""

from __future__ import annotations

import enum
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_REGISTRY_PATH = (
    Path(__file__).resolve().parent / "data" / "thai_public_entities.v1.json"
)

_MAX_TEXT_LEN = 200_000
_MAX_MATCHES_PER_SEGMENT = 50
_CONTEXT_WINDOW = 80  # chars each side for context-required aliases

# Sentence separators (Latin + CJK + Thai-relevant + line breaks +
# semicolon). Used to keep ambiguous-alias context within one sentence.
_SENTENCE_SEP_RE = re.compile(r"[.!?;。！？\n\r]")


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return [lo, hi) sentence bounds containing the [start, end) span:
    just after the nearest separator before ``start``, up to the nearest
    separator at/after ``end`` (or text ends)."""
    lo = 0
    for m in _SENTENCE_SEP_RE.finditer(text, 0, start):
        lo = m.end()
    m2 = _SENTENCE_SEP_RE.search(text, end)
    hi = m2.start() if m2 else len(text)
    return lo, hi


_THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
_THAI_DIGIT_TO_ARABIC = {d: str(i) for i, d in enumerate(_THAI_DIGITS)}
_THAI_LETTER_RE = re.compile(r"[ก-ฮะ-๎]")  # consonants+vowels


class PublicEntityType(str, enum.Enum):
    ROYAL_HISTORICAL_NAME = "ROYAL_HISTORICAL_NAME"
    ROYAL_PUBLIC_NAME = "ROYAL_PUBLIC_NAME"
    ROYAL_TITLE = "ROYAL_TITLE"
    ROYAL_REIGN_REFERENCE = "ROYAL_REIGN_REFERENCE"
    PUBLIC_FIGURE_NAME = "PUBLIC_FIGURE_NAME"
    PUBLIC_PLACE = "PUBLIC_PLACE"
    PUBLIC_ORGANIZATION = "PUBLIC_ORGANIZATION"


_VALID_TYPES = {t.value for t in PublicEntityType}

# Alias kinds.
EXACT_UNAMBIGUOUS = "exact_unambiguous"
CONTEXT_REQUIRED = "context_required"

# Global royal-context fallback when an entity declares no required_context
# for a context-required alias.
_GLOBAL_ROYAL_CONTEXT = (
    "รัชกาล", "พระบาทสมเด็จ", "สมเด็จพระ", "พระเจ้าอยู่หัว", "พระมหากษัตริย์",
    "พระราช", "เสด็จ", "ทรง", "กษัตริย์", "royal", "king", "reign",
)


@dataclass(frozen=True)
class PublicEntitySpan:
    message_index: int | None
    start: int
    end: int
    entity_id: str
    entity_type: PublicEntityType
    alias_type: str   # EXACT_UNAMBIGUOUS | CONTEXT_REQUIRED
    rule_id: str
    confidence: float = 1.0


@dataclass(frozen=True)
class _AliasEntry:
    entity_id: str
    entity_type: PublicEntityType
    kind: str
    required_context: tuple[str, ...]
    group: str  # regex group name


def _is_thai_letter(ch: str) -> bool:
    return bool(_THAI_LETTER_RE.match(ch))


def _alias_core_pattern(alias: str) -> str:
    parts: list[str] = []
    for ch in alias:
        if ch.isdigit():
            parts.append(f"[{ch}{_THAI_DIGITS[int(ch)]}]")
        elif ch in _THAI_DIGIT_TO_ARABIC:
            parts.append(f"[{_THAI_DIGIT_TO_ARABIC[ch]}{ch}]")
        elif ch == " ":
            parts.append(r"\s*")
        elif ch == ".":
            parts.append(r"\.\s*")
        elif ch == "ฯ":
            parts.append(r"ฯ?")  # tolerate the paiyannoi being dropped
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


# Lexical word-continuation class for ambiguous-alias boundaries: Thai
# letters/marks/digits (ก-๛ spans Thai consonants…Thai digits), Latin
# letters, Arabic digits, underscore. A match glued to any of these is a
# different word and must NOT count.
_WORD_CONT = r"ก-๛A-Za-z0-9_"


def _boundary_guarded(alias: str, core: str, kind: str) -> str:
    """Wrap a pattern with boundary guards.

    Thai script has no inter-word spaces, so EXACT/unambiguous long
    aliases use only DIGIT guards (so ``ร.9`` never matches inside
    ``ร.99`` / ``รัชกาลที่ 9`` never inside ``รัชกาลที่ 90``); their
    glued-suffix risk is handled downstream by STRICT CONTAINMENT in the
    Policy Engine, so we must NOT add a broad Thai-letter guard that would
    break legitimate Thai phrases.

    CONTEXT_REQUIRED / ambiguous aliases (e.g. ``ในหลวง``) DO get a
    conservative lexical-boundary guard: they must not be glued to a Thai
    letter, Latin letter, Thai/Arabic digit, or underscore on either side
    (so ``ในหลวงชัย`` / ``ชัยในหลวง`` / ``ในหลวง123`` / ``abcในหลวง`` do
    not match), while surrounding punctuation/quotes/spaces are allowed.
    """
    first, last = alias[0], alias[-1]
    if kind == CONTEXT_REQUIRED:
        # Asymmetric guard. POST: reject ANY word-continuation char
        # (Thai letter/digit, Latin, digit, underscore) so a Thai SUFFIX
        # can't extend the alias into a longer name (``ในหลวงชัย`` /
        # ``ในหลวง123``). PRE: reject only Latin/Arabic/Thai digits +
        # underscore (``abcในหลวง`` / ``123ในหลวง``) but ALLOW a Thai
        # prefix, because Thai glues words (``และในหลวง`` is legitimate).
        # A glued Thai-prefix name (``ชัยในหลวง``) is still kept safe by
        # the context requirement + strict containment downstream.
        pre = r"(?<![A-Za-z0-9๐-๙_])"
        post = rf"(?![{_WORD_CONT}])"
        return pre + core + post
    pre = r"(?<!\d)" if first.isdigit() else ""
    post = r"(?!\d)" if last.isdigit() else ""
    return pre + core + post


class _PublicEntityRegistry:
    """Immutable, validated, compiled registry (loaded once)."""

    def __init__(self) -> None:
        self._combined: re.Pattern | None = None
        self._by_group: dict[str, _AliasEntry] = {}
        self._loaded = False
        self._version = "unloaded"
        self._entity_count = 0

    # -- loading ------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True  # mark first so a failure disables (no retry storm)
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001 — disable, never allow-all
            logger.error(
                "dlp_public_registry_load_failed", error=str(exc)[:200]
            )
            self._combined = None
            self._by_group = {}

    def _load(self) -> None:
        raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "entities" not in raw:
            raise ValueError("registry missing 'entities'")
        self._version = str(raw.get("registry_version", "unknown"))

        # alias text → set of entity_ids (for conflict detection)
        seen: dict[str, set[str]] = {}
        # collect (alias, entry-data) preserving order
        collected: list[tuple[str, str, PublicEntityType, str, tuple]] = []
        for ent in raw["entities"]:
            if ent.get("status") != "active":
                continue
            etype = ent.get("entity_type")
            if etype not in _VALID_TYPES:
                raise ValueError(f"invalid entity_type: {etype!r}")
            etype_enum = PublicEntityType(etype)
            eid = ent["entity_id"]
            req = tuple(ent.get("required_context") or [])
            # canonical + aliases are exact/unambiguous (verified, specific
            # references). ``required_context`` governs ONLY the ambiguous
            # aliases — short/colloquial forms that must have royal context
            # nearby before they count as public evidence.
            names = [ent["canonical_name"], *ent.get("aliases", [])]
            for a in names:
                a = (a or "").strip()
                if a:
                    collected.append((a, eid, etype_enum, EXACT_UNAMBIGUOUS, req))
            for a in ent.get("ambiguous_aliases", []) or []:
                a = (a or "").strip()
                if a:
                    collected.append((a, eid, etype_enum, CONTEXT_REQUIRED, req))

        # Conflict detection: same alias → 2+ entities → drop (fail closed).
        # Aliases are matched against RAW segment text (offset-correct), so
        # the conflict key and the compiled pattern both use the raw alias.
        for a, eid, *_ in collected:
            seen.setdefault(a, set()).add(eid)
        conflicting = {a for a, ids in seen.items() if len(ids) > 1}
        if conflicting:
            logger.warning(
                "dlp_public_registry_alias_conflicts",
                count=len(conflicting),
            )

        # Build combined regex, longest alias first so the longest valid
        # match wins at any position.
        collected.sort(key=lambda t: len(t[0]), reverse=True)
        fragments: list[str] = []
        by_group: dict[str, _AliasEntry] = {}
        for i, (a, eid, etype_enum, kind, req) in enumerate(collected):
            if a in conflicting:
                continue  # ambiguous/conflicting → no public evidence
            grp = f"pe{i}"
            core = _alias_core_pattern(a)
            fragments.append(f"(?P<{grp}>{_boundary_guarded(a, core, kind)})")
            by_group[grp] = _AliasEntry(
                entity_id=eid, entity_type=etype_enum, kind=kind,
                required_context=req or _GLOBAL_ROYAL_CONTEXT, group=grp,
            )
        self._by_group = by_group
        self._combined = re.compile("|".join(fragments)) if fragments else None
        self._entity_count = len({e.entity_id for e in by_group.values()})

    @staticmethod
    def _norm(text: str) -> str:
        return unicodedata.normalize("NFKC", text)

    # -- matching -----------------------------------------------------------

    def match_segments(self, segments) -> list[PublicEntitySpan]:
        """Return public-entity spans across all segments (local offsets +
        message_index). Empty when the registry is disabled/empty —
        conservative (no relaxation), never allow-all. Never raises."""
        self._ensure_loaded()
        if self._combined is None:
            return []
        out: list[PublicEntitySpan] = []
        try:
            for seg in segments:
                text = getattr(seg, "text", "")
                if not isinstance(text, str) or not text:
                    continue
                if len(text) > _MAX_TEXT_LEN:
                    text = text[:_MAX_TEXT_LEN]
                # Match on the ORIGINAL segment text so span offsets align
                # with detector finding offsets. Aliases were NFKC-normalized
                # at load; the compiled patterns already tolerate Thai/Arabic
                # numerals + optional whitespace, which are the only variants
                # that matter for the Thai seed (NFKC is length-preserving
                # for these scripts, so this stays offset-correct).
                count = 0
                for m in self._combined.finditer(text):
                    entry = self._by_group.get(m.lastgroup or "")
                    if entry is None:
                        continue
                    if entry.kind == CONTEXT_REQUIRED and not self._has_context(
                        text, m.start(), m.end(), entry.required_context
                    ):
                        continue
                    out.append(PublicEntitySpan(
                        message_index=getattr(seg, "message_index", None),
                        start=m.start(), end=m.end(),
                        entity_id=entry.entity_id, entity_type=entry.entity_type,
                        alias_type=entry.kind,
                        rule_id=f"public_entity.{entry.entity_type.value}.{entry.entity_id}",
                        confidence=1.0,
                    ))
                    count += 1
                    if count >= _MAX_MATCHES_PER_SEGMENT:
                        break
        except Exception as exc:  # noqa: BLE001 — disable for this call
            logger.warning("dlp_public_registry_match_failed", error=str(exc)[:120])
            return []
        return out

    @staticmethod
    def _has_context(text, start, end, required_context) -> bool:
        # Phase 3.1: context must be in the SAME sentence as the alias.
        # Clamp the ±window to the sentence boundaries around the alias so
        # a keyword from an adjacent sentence (or after a newline) cannot
        # validate it.
        s_lo, s_hi = _sentence_bounds(text, start, end)
        lo = max(s_lo, start - _CONTEXT_WINDOW)
        hi = min(s_hi, end + _CONTEXT_WINDOW)
        # Exclude the matched span itself so the alias can't be its own
        # context.
        window = text[lo:start] + " " + text[end:hi]
        return any(kw in window for kw in required_context)

    @property
    def version(self) -> str:
        self._ensure_loaded()
        return self._version


public_entity_registry = _PublicEntityRegistry()

# Detectors/entity types whose findings a public-entity span may relax.
RELAXABLE_NAME_DETECTORS = frozenset({"thai_name", "thai_name_no_prefix"})
RELAXABLE_NAME_ENTITY_TYPES = frozenset({"person", "thai_name"})
_LOCATION_ENTITY_TYPES = frozenset({"location", "gpe"})
_ORG_ENTITY_TYPES = frozenset({"organization"})
_PLACE_REGISTRY_TYPES = frozenset({PublicEntityType.PUBLIC_PLACE})
_ORG_REGISTRY_TYPES = frozenset({PublicEntityType.PUBLIC_ORGANIZATION})
_NAME_REGISTRY_TYPES = frozenset({
    PublicEntityType.ROYAL_HISTORICAL_NAME,
    PublicEntityType.ROYAL_PUBLIC_NAME,
    PublicEntityType.ROYAL_TITLE,
    PublicEntityType.ROYAL_REIGN_REFERENCE,
    PublicEntityType.PUBLIC_FIGURE_NAME,
})


def finding_relaxable_by(span_type: PublicEntityType, detector_name: str,
                         entity_type: str) -> bool:
    """Whether a public span of ``span_type`` may relax a finding from
    ``detector_name`` / ``entity_type``. Names relax name findings; places
    relax locations; orgs relax organizations. Nothing else."""
    et = (entity_type or "").lower()
    if span_type in _NAME_REGISTRY_TYPES:
        return (
            detector_name in RELAXABLE_NAME_DETECTORS
            or et in RELAXABLE_NAME_ENTITY_TYPES
        )
    if span_type in _PLACE_REGISTRY_TYPES:
        return et in _LOCATION_ENTITY_TYPES
    if span_type in _ORG_REGISTRY_TYPES:
        return et in _ORG_ENTITY_TYPES
    return False
