"""The layout catalogue and the one place where a layout is chosen (AM21).

Each layout in look.json declares what it accepts — how many characters in a heading, how many items,
how many rows before it must break, how many chart series. ``choose`` takes a block of content and its
meaning, scores every layout that takes that kind of content by how well it holds it, and returns the
best with a recorded reason; a tie breaks the same way every time (the catalogue's own order), so the
same content always gets the same layout.

Too much for the layout, in order: the next size down the scale (a layout marked ``shrink`` takes the
same content at the small size); a break into parts across slides or pages where the layout counts
items, rows or lines; and when nothing holds it, ``DoesNotFit`` — the run says the content does not fit
and names the section. Nothing is truncated here or anywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .measure import latin_columns, width_ratio
from .tokens import LOOK

# fields that count things (break into parts when over), and the field that measures one of them
COUNTED = {"items": "item", "rows": "cell", "lines": "line", "figures": "figure", "categories": None, "series": None, "columns": None}
# text fields the small size may hold more of
TEXT = {"title", "subtitle", "label", "value", "sentence", "delta", "lead", "item", "text", "cell", "caption", "line", "paragraph",
        "entry", "note", "sheet_name", "figure"}
TARGET_FILL = 0.8


class DoesNotFit(Exception):
    """Content too long for every layout in the catalogue: the section is named, and no file is written."""

    def __init__(self, section: str, why: str):
        super().__init__(f"{section}: {why}")
        self.section = section
        self.why = why


@dataclass
class Choice:
    layout: str
    role: str                 # the text role the layout is set at: body, or small when it had to shrink
    fill: float               # how full the layout is (1.0 = at its limit)
    reason: str               # recorded: why this layout, in plain words
    parts: list = field(default_factory=list)  # the content, broken into what each slide or page holds


def measure(content: dict) -> dict[str, float]:
    """What there is of each field: a string's width in Latin columns (AM22 §1.4: the characters a reader
    sees, a Thai one weighed by how much wider it is — never bare len() on a display string), a list's
    count, and the widest member of a list under the name its layout measures it by (items → item,
    rows → cell, lines → line)."""
    out: dict[str, float] = {}
    for k, v in content.items():
        if isinstance(v, str):
            out[k] = latin_columns(v.strip())
        elif isinstance(v, (list, tuple)):
            out[k] = len(v)
            one = COUNTED.get(k)
            if one and v:
                if k == "rows":
                    out[one] = max((latin_columns(str(c or "")) for row in v for c in row), default=0)
                    out.setdefault("columns", max((len(row) for row in v), default=0))
                else:
                    out[one] = max((latin_columns(str(x or "")) for x in v), default=0)
        elif isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def _fits(takes: dict, got: dict, scale: float) -> tuple[bool, float, list[str]]:
    """Whether a layout holds the content (text limits widened by ``scale`` at a smaller size), how full
    it is, and which fields are over."""
    fill = 0.0
    over: list[str] = []
    for key, limit in takes.items():
        if key.endswith("_min"):
            name = key[:-4]
            if got.get(name, 0) < limit:
                over.append(f"{name} {int(got.get(name, 0))} < {limit}")
            continue
        if key not in got:
            continue
        lim = limit * scale if key in TEXT else limit
        f = got[key] / lim if lim else 0.0
        fill = max(fill, f)
        if f > 1.0:
            over.append(f"{key} {int(got[key])} > {int(lim)}")
    return not over, fill, over


def _parts(content: dict, takes: dict) -> list[dict]:
    """The content broken into what each slide or page holds, by the counted field the layout limits."""
    for key in ("items", "rows", "lines", "figures"):
        if key in takes and isinstance(content.get(key), (list, tuple)) and len(content[key]) > takes[key]:
            n = takes[key]
            seq = list(content[key])
            return [{**content, key: seq[i:i + n]} for i in range(0, len(seq), n)]
    return [content]


def choose(fmt: str, content: dict, section: str, kinds: tuple | None = None) -> Choice:
    """The layout for a block of content. ``content`` holds the block's fields by the names the catalogue
    uses (title, items, rows, series…); ``kinds`` narrows the candidates to those layouts, else every
    layout that takes every field the content has is a candidate."""
    cat = LOOK.catalogue(fmt)
    got = measure(content)
    fields = {k for k in got if k not in ("columns",) or "columns" in content}
    scored: list[tuple[float, int, str, str, float, list]] = []
    reasons: list[str] = []
    shrink = LOOK.pt(fmt, "body") / LOOK.pt(fmt, "small")
    for order, (name, lay) in enumerate(cat.items()):
        if kinds is not None and name not in kinds:
            continue
        takes = lay["takes"]
        accepted = set(takes) | {k[:-4] for k in takes if k.endswith("_min")} | {"columns"}
        if kinds is None and not fields <= accepted:
            continue
        # a counted field over its limit breaks into parts; the fill is of one part
        parts = _parts(content, takes)
        if len(parts) > takes.get("parts_max", len(parts)):
            # AM23 §1.5: PowerPoint's ladder ends in spilling — this one ends in a refusal: a deck's table breaks across
            # at most parts_max slides, and more than that is not a deck (the run offers what can be made instead)
            reasons.append(f"{name}: {len(parts)} parts > {takes['parts_max']}")
            continue
        part = measure(parts[0])
        for role, scale in (("body", 1.0), ("small", shrink)):
            if role == "small" and not lay.get("shrink"):
                break
            ok, fill, over = _fits(takes, part, scale)
            if ok:
                why = f"{name} at {role}: {fill:.2f} full"
                if len(parts) > 1:
                    why += f", in {len(parts)} parts"
                scored.append((abs(fill - TARGET_FILL), order, name, role, fill, parts))
                reasons.append(why)
                break
            reasons.append(f"{name} at {role}: {', '.join(over)}")
    if not scored:
        raise DoesNotFit(section, "; ".join(reasons) or f"no layout of the {fmt} catalogue takes {sorted(fields)}")
    scored.sort(key=lambda s: (s[0], s[1]))
    _, _, name, role, fill, parts = scored[0]
    reason = f"{name} at {role} ({fill:.2f} full" + (f", {len(parts)} parts" if len(parts) > 1 else "") + ")"
    if len(scored) > 1:
        reason += " over " + ", ".join(s[2] for s in scored[1:])
    return Choice(layout=name, role=role, fill=round(fill, 3), reason=reason, parts=parts)


# the layouts a commentary is printed in, per format: the summary's paragraph, a point of a list, the
# most points a slide or a page holds before the layout breaks them (per_page), and the most a heading may
# hold at all (ceiling: the report's prose block refuses a list over twenty items; the commentary sheet holds
# twelve under one heading; a deck has no ceiling of its own — its list breaks across slides)
_WRITING = {"pptx": {"summary": ("statement", "text"), "item": ("bullets", "item"), "per_page": ("bullets", "items"),
                     "sentence": ("figure", "sentence")},
            "docx": {"summary": ("prose", "paragraph"), "item": ("prose", "item"), "per_page": ("prose", "items"),
                     "ceiling": ("prose", "items")},
            "xlsx": {"summary": ("commentary", "text"), "item": ("commentary", "item"), "per_page": ("commentary", "items"),
                     "ceiling": ("commentary", "items")}}
# a commentary printed in a deck is printed in its report too (financial_report): the tighter of the two holds
_PRINTED_WITH = {"pptx": ("pptx", "docx"), "docx": ("docx",), "xlsx": ("xlsx",)}


def writing_limits(fmt: str = "pptx", lang: str | None = None) -> dict:
    """What a model is told before it writes (AM21 §6.1, AM22 §1.2–1.4): the catalogue's limits for the
    words it produces, in the characters a reader sees in the run's language — the summary's paragraph
    (``summary_chars``), a point of a list (``bullet_chars``), how many points a slide or a page holds
    before the list breaks (``bullets_max``) and the most a heading may hold at all (``items_max``). The
    validator (runner: checkNarrative) measures the answer against these same numbers; an item past them
    is refused and asked for again, never trimmed.

    The catalogue's numbers are Latin columns. Told in Thai they are divided by how much wider a Thai
    column is in the family (measure.width_ratio), so a Thai run and an English run are allowed the
    same width of text — which is what a frame holds."""
    from ...engine import words

    lang = words.lang_of(lang if lang is not None else words.lang())
    ratio = width_ratio(lang)

    def limit(which: str) -> int:
        vals = []
        for f in _PRINTED_WITH[fmt]:
            spec = _WRITING[f].get(which)
            if spec:
                vals.append(LOOK.catalogue(f)[spec[0]]["takes"][spec[1]])
        return min(vals)

    per_page = _WRITING[fmt]["per_page"]
    out = {"summary_chars": int(limit("summary") / ratio), "bullet_chars": int(limit("item") / ratio),
           "bullets_max": LOOK.catalogue(fmt)[per_page[0]]["takes"][per_page[1]],
           "items_max": limit("ceiling")}
    # (numbers only: the runner reads this map as numbers — computedView.WritingLimits map[string]int — and a word in
    # it fails the reading of the whole table. AM22's model measurement found that before any run did.)
    if "sentence" in _WRITING[fmt]:
        out["sentence_chars"] = int(limit("sentence") / ratio)
    return out


def listing(fmt: str) -> list[dict]:
    """The catalogue as a list a report prints: the layout, what it is for, what it refuses."""
    return [{"layout": name, "for": lay["for"], "takes": lay["takes"], "refuses": lay["refuses"]} for name, lay in LOOK.catalogue(fmt).items()]


def slides_for(n_items: int, per: int) -> int:
    return max(1, math.ceil(n_items / per))
