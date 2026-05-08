"""DLP text preprocessor — encoding bypass protection.

Runs BEFORE detectors to neutralize common bypass techniques:
1. Unicode normalization (NFKC)
2. Zero-width character stripping
3. Homoglyph/confusable mapping (Cyrillic → Latin)
4. Base64 detection and decode-then-scan

The preprocessor produces a ``NormalizedText`` that the registry scans.
Base64 segments are decoded and scanned separately; any findings are
mapped back to the original base64 span positions.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Zero-width characters to strip (safe to remove without affecting Thai)
# Thai combining marks (U+0E31, U+0E34-U+0E3A, U+0E47-U+0E4E) are NOT here.
# ---------------------------------------------------------------------------

_ZERO_WIDTH_CHARS = frozenset([
    "​",  # Zero-width space
    "‌",  # Zero-width non-joiner
    "‍",  # Zero-width joiner
    "﻿",  # BOM / zero-width no-break space
    "­",  # Soft hyphen
    "⁠",  # Word joiner
    "᠎",  # Mongolian vowel separator
    "⁣",  # Invisible separator
    "⁤",  # Invisible plus
])

# ---------------------------------------------------------------------------
# Homoglyph mapping: Cyrillic lookalikes → Latin equivalents
# Covers the most common confusable characters used in bypass attacks.
# ---------------------------------------------------------------------------

_HOMOGLYPH_MAP: dict[str, str] = {
    "а": "a",  # Cyrillic а → Latin a
    "е": "e",  # Cyrillic е → Latin e
    "о": "o",  # Cyrillic о → Latin o
    "р": "p",  # Cyrillic р → Latin p
    "с": "c",  # Cyrillic с → Latin c
    "у": "y",  # Cyrillic у → Latin y (visual match)
    "х": "x",  # Cyrillic х → Latin x
    "і": "i",  # Cyrillic і → Latin i
    "ј": "j",  # Cyrillic ј → Latin j
    "һ": "h",  # Cyrillic һ → Latin h
    "А": "A",  # Cyrillic А → Latin A
    "В": "B",  # Cyrillic В → Latin B
    "Е": "E",  # Cyrillic Е → Latin E
    "К": "K",  # Cyrillic К → Latin K
    "М": "M",  # Cyrillic М → Latin M
    "Н": "H",  # Cyrillic Н → Latin H
    "О": "O",  # Cyrillic О → Latin O
    "Р": "P",  # Cyrillic Р → Latin P
    "С": "C",  # Cyrillic С → Latin C
    "Т": "T",  # Cyrillic Т → Latin T
    "Х": "X",  # Cyrillic Х → Latin X
}

_HOMOGLYPH_TRANS = str.maketrans(_HOMOGLYPH_MAP)

# ---------------------------------------------------------------------------
# Base64 detection pattern
# At least 20 chars of base64 alphabet ending with optional = or ==
# Must be word-bounded to avoid matching substrings of normal text.
# ---------------------------------------------------------------------------

_BASE64_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])"
    r"([A-Za-z0-9+/]{20,}={0,2})"
    r"(?![A-Za-z0-9+/=])"
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class NormalizedText:
    """Result of preprocessing a text for DLP scanning.

    Attributes
    ----------
    normalized : str
        Text after NFKC normalization + zero-width stripping + homoglyph mapping.
        This is what detectors scan against.
    original : str
        The original unmodified input text.
    offset_map : list[int] | None
        Maps each index in ``normalized`` back to the index in ``original``.
        None if no characters were removed (lengths are equal).
    base64_segments : list[Base64Segment]
        Decoded base64 segments found in the text, ready for separate scanning.
    transformations : list[str]
        Human-readable list of transformations that were applied.
    """

    normalized: str
    original: str
    offset_map: list[int] | None = None
    base64_segments: list["Base64Segment"] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)


@dataclass
class Base64Segment:
    """A decoded base64 segment with position info in the original text."""

    original_start: int
    original_end: int
    encoded_text: str
    decoded_text: str


# ---------------------------------------------------------------------------
# Preprocessing pipeline
# ---------------------------------------------------------------------------

def normalize_for_scan(text: str) -> NormalizedText:
    """Preprocess text for DLP scanning.

    Applies (in order):
    1. Unicode NFKC normalization (with offset tracking)
    2. Zero-width character stripping (preserving Thai combining marks)
    3. Homoglyph mapping (Cyrillic confusables → Latin)
    4. Base64 segment extraction and decoding

    Returns a NormalizedText with the cleaned text and metadata.
    """
    transformations: list[str] = []
    original = text

    # Step 1: NFKC normalization — fullwidth chars, compatibility forms.
    # Thai Sara Am (U+0E33) decomposes into 2 chars (U+0E4D + U+0E32)
    # under NFKC, shifting all subsequent offsets.  Build an offset map
    # so detector findings can be remapped to the original text.
    nfkc = unicodedata.normalize("NFKC", text)
    nfkc_map: list[int] | None = None
    if nfkc != text:
        transformations.append("unicode_nfkc")
        nfkc_map = []
        for orig_idx, ch in enumerate(text):
            nfkc_ch = unicodedata.normalize("NFKC", ch)
            for _ in nfkc_ch:
                nfkc_map.append(orig_idx)
    text = nfkc

    # Step 2: Zero-width character stripping with offset mapping
    stripped_chars: list[str] = []
    zw_map: list[int] = []
    had_zero_width = False

    for i, ch in enumerate(text):
        if ch in _ZERO_WIDTH_CHARS:
            had_zero_width = True
            continue
        stripped_chars.append(ch)
        zw_map.append(i)

    if had_zero_width:
        text = "".join(stripped_chars)
        transformations.append("zero_width_stripped")

    # Compose offset maps: final normalized position → original position.
    final_map: list[int] | None = None
    if had_zero_width and nfkc_map is not None:
        final_map = [nfkc_map[j] for j in zw_map]
    elif had_zero_width:
        final_map = zw_map
    elif nfkc_map is not None:
        final_map = nfkc_map

    # Step 3: Homoglyph mapping
    mapped = text.translate(_HOMOGLYPH_TRANS)
    if mapped != text:
        transformations.append("homoglyph_mapped")
    text = mapped

    # Step 4: Base64 segment extraction
    base64_segments = _extract_base64_segments(text)
    if base64_segments:
        transformations.append("base64_decoded")

    return NormalizedText(
        normalized=text,
        original=original,
        offset_map=final_map,
        base64_segments=base64_segments,
        transformations=transformations,
    )


def _extract_base64_segments(text: str) -> list[Base64Segment]:
    """Find and decode base64-encoded segments in the text.

    Only returns segments that decode to valid UTF-8 text with mostly
    printable characters (to avoid false-positive on binary data or
    short random strings).
    """
    segments: list[Base64Segment] = []

    for m in _BASE64_RE.finditer(text):
        encoded = m.group(1)

        # Pad if needed for valid base64
        padded = encoded
        pad_needed = len(padded) % 4
        if pad_needed:
            padded += "=" * (4 - pad_needed)

        try:
            raw_bytes = base64.b64decode(padded, validate=True)
        except Exception:
            continue

        # Must decode as valid UTF-8
        try:
            decoded = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue

        # Reject binary-looking output: must be mostly printable
        if not decoded or len(decoded) < 4:
            continue
        printable_ratio = sum(
            1 for c in decoded if c.isprintable() or c in "\n\r\t "
        ) / len(decoded)
        if printable_ratio < 0.7:
            continue

        segments.append(Base64Segment(
            original_start=m.start(1),
            original_end=m.end(1),
            encoded_text=encoded,
            decoded_text=decoded,
        ))

    return segments


def map_offset_to_original(
    normalized_offset: int,
    offset_map: list[int] | None,
) -> int:
    """Map an offset in normalized text back to the original text."""
    if offset_map is None:
        return normalized_offset
    if normalized_offset >= len(offset_map):
        # Beyond the end — return last original position + 1
        return offset_map[-1] + 1 if offset_map else normalized_offset
    return offset_map[normalized_offset]
