"""Measure text against its box with the real font (Pillow + Sarabun).

Used twice: the deck's fit pass shrinks text that would not fit, and the
quality gate fails any slide whose text still overflows. Thai has no spaces
between words, so a Thai run may break between any two characters here —
that is optimistic, so the width gets a 12% safety margin.
"""

from __future__ import annotations

import functools
import re
import unicodedata

from .theme import FONT_FILES

EMU_PER_PT = 12700
EMU_PER_IN = 914400
SAFETY = 1.12
LINE = 1.22


@functools.lru_cache(maxsize=64)
def _font(face: str, bold: bool, size_pt: float):
    from PIL import ImageFont

    files = FONT_FILES.get(face) or FONT_FILES["Sarabun"]
    path = files["bold" if bold else "regular"]
    # Measure at 10x for precision, scale back.
    return ImageFont.truetype(path, max(1, int(round(size_pt * 10))))


def text_width_pt(text: str, face: str, size_pt: float, bold: bool = False) -> float:
    if not text:
        return 0.0
    return _font(face, bold, size_pt).getlength(text) / 10.0


def _tokens(text: str) -> list[str]:
    # Latin words stay whole; Thai characters (with their combining marks) break anywhere.
    out: list[str] = []
    for m in re.finditer(r"[A-Za-z0-9,.\-+%/()]+|\s+|.", text):
        tok = m.group(0)
        if out and tok and unicodedata.category(tok[0]) in ("Mn", "Me"):
            out[-1] += tok
        else:
            out.append(tok)
    return out


def wrap_lines(text: str, face: str, size_pt: float, width_pt: float, bold: bool = False) -> int:
    lines = 0
    for para in (text or "").split("\n"):
        lines += 1
        cur = 0.0
        for tok in _tokens(para):
            w = text_width_pt(tok, face, size_pt, bold) * SAFETY
            if cur + w > width_pt and cur > 0:
                lines += 1
                cur = w if not tok.isspace() else 0.0
            else:
                cur += w
    return lines


def frame_overflow(tf, box_w_emu: int, box_h_emu: int, default_face: str = "Sarabun") -> float:
    """Needed height ÷ available height for a python-pptx text frame (>1 = overflow)."""
    left = tf.margin_left if tf.margin_left is not None else int(0.1 * EMU_PER_IN)
    right = tf.margin_right if tf.margin_right is not None else int(0.1 * EMU_PER_IN)
    top = tf.margin_top if tf.margin_top is not None else int(0.05 * EMU_PER_IN)
    bottom = tf.margin_bottom if tf.margin_bottom is not None else int(0.05 * EMU_PER_IN)
    width_pt = (box_w_emu - left - right) / EMU_PER_PT
    avail_pt = (box_h_emu - top - bottom) / EMU_PER_PT
    need = 0.0
    for p in tf.paragraphs:
        text = "".join(r.text for r in p.runs)
        if not p.runs:
            need += 12 * LINE
            continue
        size = max((r.font.size.pt if r.font.size else 18) for r in p.runs)
        bold = any(bool(r.font.bold) for r in p.runs)
        face = next((r.font.name for r in p.runs if r.font.name), default_face)
        indent = 18 if p._pPr is not None and p._pPr.get("marL") else 0
        lines = wrap_lines(text, face, size, width_pt - indent, bold)
        need += lines * size * LINE
    if avail_pt <= 0:
        return 99.0
    return need / avail_pt


def shrink_to_fit(shape, min_ratio: float = 0.6, face: str = "Sarabun") -> float:
    """Scale every run in a text shape down until it fits (never below min_ratio)."""
    from pptx.util import Pt

    tf = shape.text_frame
    ratio = frame_overflow(tf, shape.width, shape.height, face)
    if ratio <= 1.0:
        return ratio
    base = [[(r, r.font.size.pt if r.font.size else 18) for r in p.runs] for p in tf.paragraphs]
    scale = 1.0
    while ratio > 1.0 and scale > min_ratio:
        scale = round(scale - 0.05, 2)
        for runs in base:
            for r, size in runs:
                r.font.size = Pt(max(7, round(size * scale, 1)))
        ratio = frame_overflow(tf, shape.width, shape.height, face)
    return ratio
