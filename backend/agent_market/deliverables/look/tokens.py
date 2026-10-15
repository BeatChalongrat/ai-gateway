"""The type scale, the palettes and the layout limits, read from look.json — and refused outside it.

A renderer asks for a size by its role (``pt("docx", "body")``) and a colour by its token
(``color(palette, "rule")``); a role or a token that is not in look.json raises ``LookError`` with the
scale or the palette named, never a fallback. The number a role is in points is nobody's to type into
code: it is read from the file.
"""

from __future__ import annotations

import copy
import functools
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "look.json")
_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


class LookError(ValueError):
    """A size, a colour, a theme or a layout that look.json does not have."""


@functools.lru_cache(maxsize=1)
def _data() -> dict:
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


class Look:
    def __init__(self, data: dict):
        self.d = data
        self.roles: tuple = tuple(data["scale"]["roles"])
        self.formats: tuple = tuple(data["scale"]["pt"])
        self.family: str = data["fonts"]["family"]
        self.font_files: dict = dict(data["fonts"]["files"])
        for fmt, sizes in data["scale"]["pt"].items():
            if tuple(sizes) != self.roles:
                raise LookError(f"look.json: the {fmt} scale does not name the roles {self.roles}")
            if list(sizes.values()) != sorted(sizes.values(), reverse=True):
                raise LookError(f"look.json: the {fmt} scale must go down from display to small")
        for tid, t in data["themes"].items():
            for k, v in t["palette"].items():
                if not isinstance(v, str) or not _HEX.match(v):
                    raise LookError(f"look.json: themes.{tid}.palette.{k} must be a 6-digit hex colour")
            for tok in t["chart"]:
                if tok not in t["palette"]:
                    raise LookError(f"look.json: themes.{tid}.chart names {tok!r}, which is not in its palette")
            for key in ("header", "header_text", "strong"):
                if t["table"][key] not in t["palette"]:
                    raise LookError(f"look.json: themes.{tid}.table.{key} names {t['table'][key]!r}, which is not in its palette")

    # ── the scale ────────────────────────────────────────────────────
    def pt(self, fmt: str, role: str) -> float:
        """The size of a role in a format, in points."""
        try:
            return float(self.d["scale"]["pt"][fmt][role])
        except KeyError:
            raise LookError(f"no size {role!r} for {fmt!r}: the scale is {' · '.join(self.roles)} for {' · '.join(self.formats)}") from None

    def sizes(self, fmt: str) -> list[float]:
        return [float(v) for v in self.d["scale"]["pt"][fmt].values()]

    def role_of(self, fmt: str, pt: float) -> str:
        """The role a size is — or a refusal: a size that is not in the scale is nobody's to use."""
        for role, v in self.d["scale"]["pt"][fmt].items():
            if abs(float(v) - float(pt)) < 0.01:
                return role
        raise LookError(f"{pt} pt is not in the {fmt} scale {self.sizes(fmt)}")

    def smaller(self, role: str) -> str | None:
        """The next role down the scale, or None at the bottom."""
        i = self.roles.index(role)
        return self.roles[i + 1] if i + 1 < len(self.roles) else None

    def bold(self, role: str) -> bool:
        try:
            return self.d["scale"]["weight"][role] == "bold"
        except KeyError:
            raise LookError(f"no weight for role {role!r}") from None

    def line(self, fmt: str) -> float:
        return float(self.d["scale"]["line"][fmt])

    # ── the themes ───────────────────────────────────────────────────
    @property
    def theme_ids(self) -> list[str]:
        return list(self.d["themes"])

    @property
    def default_theme(self) -> str:
        return self.d["default_theme"]

    def theme(self, tid: str) -> dict:
        try:
            return copy.deepcopy(self.d["themes"][tid])
        except KeyError:
            raise LookError(f"no theme {tid!r}: the themes are {', '.join(self.theme_ids)}") from None

    @staticmethod
    def color(palette: dict, token: str) -> str:
        """A palette token's colour, six hex digits; a token the palette does not have is refused."""
        try:
            return palette[token].upper()
        except KeyError:
            raise LookError(f"no colour {token!r}: the palette is {', '.join(palette)}") from None

    # ── the catalogue ────────────────────────────────────────────────
    def page(self, fmt: str) -> dict:
        return copy.deepcopy(self.d["layouts"][fmt]["page"])

    def catalogue(self, fmt: str) -> dict:
        try:
            return copy.deepcopy(self.d["layouts"][fmt]["catalogue"])
        except KeyError:
            raise LookError(f"no layout catalogue for {fmt!r}") from None

    def layout(self, fmt: str, name: str) -> dict:
        cat = self.catalogue(fmt)
        try:
            return cat[name]
        except KeyError:
            raise LookError(f"no layout {name!r} for {fmt!r}: the catalogue is {', '.join(cat)}") from None

    def brand(self) -> dict:
        return copy.deepcopy(self.d["brand"])


LOOK = Look(_data())


def pt(fmt: str, role: str) -> float:
    return LOOK.pt(fmt, role)


def color(palette: dict, token: str) -> str:
    return Look.color(palette, token)


def rgb(hex6: str) -> tuple[int, int, int]:
    h = hex6.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(hex6: str) -> float:
    def ch(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb(hex6)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two colours (1 … 21)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)
