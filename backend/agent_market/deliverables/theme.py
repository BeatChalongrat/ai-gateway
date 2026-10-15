"""Theme registry for deliverables.

A theme is the org's look: a palette, the chart palette in a fixed series order, a cover treatment, a
table style, a footer note and an optional logo. The built-in themes are in look/look.json (AM21: the
one file a person opens to change the look of everything); one theme per org, versioned — a run records
the theme id and version it rendered with, so a re-render of an old run looks like the original unless
the user asks for the new look.

An organisation's own brand (AM21 §6.3) is laid over its theme: a primary colour, an accent, a typeface,
a logo. Each is refused with its reason when it would make the document unreadable — a colour that
fails contrast against the surface it sits on, a typeface the sandbox does not have or one without Thai
glyphs — and the theme's own value stays; ``resolve`` records the refusals under ``refused`` so the run
can say them.
"""

from __future__ import annotations

import copy
import re

from .look.tokens import LOOK, LookError, contrast

# the built-in themes, by id and the key of their name (the runner's progress card reads this table from
# this file; what each looks like is look.json's)
PRESETS: dict[str, dict] = {
    "navy": {"id": "navy", "name": "docs.theme.navy"},
    "teal": {"id": "teal", "name": "docs.theme.teal"},
    "charcoal": {"id": "charcoal", "name": "docs.theme.charcoal"},
}
for _tid in list(PRESETS):
    _t = LOOK.theme(_tid)
    if _t["name"] != PRESETS[_tid]["name"]:
        raise LookError(f"theme {_tid}: look.json names it {_t['name']!r}, theme.py {PRESETS[_tid]['name']!r}")
    PRESETS[_tid].update({"palette": _t["palette"], "chart": _t["chart"], "cover": _t["cover"], "table": _t["table"]})
if set(PRESETS) != set(LOOK.theme_ids):
    raise LookError(f"theme.py lists {sorted(PRESETS)}, look.json {sorted(LOOK.theme_ids)}")

FONT_FILES = {LOOK.family: dict(LOOK.font_files)}

DEFAULT_THEME = {
    "id": LOOK.default_theme,
    "version": 1,
    "company": "",
    "logo": None,
    "fonts": {"thai": LOOK.family, "latin": LOOK.family},
    "footer_note": "docs.footer",
}

# what an organisation may lay over its theme: these palette tokens, by name
BRAND_TOKENS = ("primary", "accent")


def said(value):
    """A preset's word — a theme's name, the footer's note — said in the run's language; any other text as it is."""
    from ..engine.words import SHARED as S

    return S(value) if isinstance(value, str) and value in S.strings else value


_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


class ThemeError(ValueError):
    pass


def brand_refusals(theme: dict, preset: dict) -> tuple[dict, list[dict]]:
    """The organisation's own values laid over the preset's, and the ones refused with their reason:
    (palette, [{what, value, why}]). A refused value is not used; the preset's stays."""
    from ..engine.words import SHARED as S

    palette = dict(preset["palette"])
    refused: list[dict] = []
    least = LOOK.brand()["contrast_min"]
    for k, v in (theme.get("palette") or {}).items():
        if k not in BRAND_TOKENS:
            refused.append({"what": f"palette.{k}", "value": str(v), "why": S("brand.refuse.token", token=k, tokens=", ".join(BRAND_TOKENS))})
            continue
        if not isinstance(v, str) or not _HEX.match(v.lstrip("#")):
            refused.append({"what": f"palette.{k}", "value": str(v), "why": S("brand.refuse.hex")})
            continue
        hex6 = v.lstrip("#").upper()
        # the primary carries white text (a cover band, a table header) and sits on the paper; the accent
        # is text on the paper
        against = [("on_primary", palette["on_primary"]), ("paper", palette["paper"])] if k == "primary" else [("paper", palette["paper"])]
        low = [(name, contrast(hex6, other)) for name, other in against if contrast(hex6, other) < least]
        if low:
            name, ratio = low[0]
            refused.append({"what": f"palette.{k}", "value": hex6, "why": S("brand.refuse.contrast", ratio=f"{ratio:.1f}", least=f"{least:.1f}", against=name)})
            continue
        palette[k] = hex6
    fonts = theme.get("fonts") or {}
    for role, face in fonts.items():
        if face != LOOK.family:
            refused.append({"what": f"fonts.{role}", "value": str(face), "why": S("brand.refuse.font", face=str(face), family=LOOK.family)})
    return palette, refused


def resolve(theme: dict | None) -> dict:
    """Merge an org theme over its preset and the defaults; validate; record what of the brand was refused."""
    t = copy.deepcopy(DEFAULT_THEME)
    theme = theme or {}
    preset = PRESETS.get(theme.get("id") or theme.get("preset") or LOOK.default_theme) or PRESETS[LOOK.default_theme]
    t.update({k: v for k, v in theme.items() if k not in ("palette", "fonts", "chart", "cover", "table")})
    t["id"] = preset["id"]
    t["name"] = theme.get("name") or said(preset["name"])   # an organisation's own name as given; a preset's said
    t["footer_note"] = said(t["footer_note"])
    t["palette"], t["refused"] = brand_refusals(theme, preset)
    t["chart"] = list(preset["chart"])
    t["cover"] = preset["cover"]
    t["table"] = dict(preset["table"])
    t["fonts"] = {"thai": LOOK.family, "latin": LOOK.family}
    for k, v in t["palette"].items():
        if not isinstance(v, str) or not _HEX.match(v):
            raise ThemeError(f"palette.{k} must be a 6-digit hex colour")
    return t


def rgb(hex6: str) -> tuple[int, int, int]:
    h = hex6.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mpl(hex6: str) -> str:
    return "#" + hex6.lstrip("#")
