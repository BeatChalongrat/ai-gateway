"""Theme registry for deliverables.

A theme is the org's look: palette, font pair, cover style, table style,
footer note and an optional logo. One theme per org, versioned — a run
records the theme id and version it rendered with, so a re-render of an old
run looks like the original unless the user asks for the new look.

Only fonts that ship with the sandbox image are allowed (Sarabun covers Thai
and Latin); the quality gate fails any run whose text uses another face.
"""

from __future__ import annotations

import copy
import re

FONT_FILES = {
    "Sarabun": {"regular": "/sandbox/scripts/fonts/Sarabun-Regular.ttf",
                "bold": "/sandbox/scripts/fonts/Sarabun-Bold.ttf"},
}

PRESETS: dict[str, dict] = {
    "navy": {
        "id": "navy", "name": "กรมท่า",
        "palette": {"primary": "12264A", "primary_dark": "0B1A33", "accent": "2F6FD6", "accent_soft": "E3ECFB",
                    "gold": "C99A2E", "positive": "1E7F55", "negative": "B93A32", "ink": "1C2230",
                    "muted": "5F6B7A", "rule": "D5DBE4", "surface": "F3F5F9", "on_primary": "FFFFFF"},
    },
    "teal": {
        "id": "teal", "name": "เขียวหยก",
        "palette": {"primary": "0F4C4C", "primary_dark": "093333", "accent": "1C8C84", "accent_soft": "DDF0EE",
                    "gold": "D08C2E", "positive": "1E7F55", "negative": "B5413A", "ink": "1B2626",
                    "muted": "5C6B6A", "rule": "D3DEDD", "surface": "F2F7F6", "on_primary": "FFFFFF"},
    },
    "charcoal": {
        "id": "charcoal", "name": "ถ่านหิน",
        "palette": {"primary": "24272E", "primary_dark": "16181C", "accent": "D9642B", "accent_soft": "FBE9DF",
                    "gold": "B8862B", "positive": "2A7A4F", "negative": "B23A31", "ink": "1E2024",
                    "muted": "656B75", "rule": "DADDE2", "surface": "F4F4F5", "on_primary": "FFFFFF"},
    },
}

DEFAULT_THEME = {
    "id": "navy",
    "version": 1,
    "company": "",
    "logo": None,
    "fonts": {"thai": "Sarabun", "latin": "Sarabun"},
    "cover": {"style": "band"},
    "table": {"zebra": True},
    "footer_note": "จัดทำอัตโนมัติ · ตัวเลขคำนวณด้วยสคริปต์ ไม่ได้ให้โมเดลคำนวณ",
}

_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


class ThemeError(ValueError):
    pass


def resolve(theme: dict | None) -> dict:
    """Merge an org theme over its preset and the defaults; validate."""
    t = copy.deepcopy(DEFAULT_THEME)
    theme = theme or {}
    preset = PRESETS.get(theme.get("id") or theme.get("preset") or "navy") or PRESETS["navy"]
    t.update({k: v for k, v in theme.items() if k not in ("palette", "fonts")})
    t["id"] = preset["id"]
    t["name"] = theme.get("name") or preset["name"]
    t["palette"] = {**preset["palette"], **(theme.get("palette") or {})}
    t["fonts"] = {**DEFAULT_THEME["fonts"], **(theme.get("fonts") or {})}
    for k, v in t["palette"].items():
        if not isinstance(v, str) or not _HEX.match(v.lstrip("#")):
            raise ThemeError(f"palette.{k} must be a 6-digit hex colour")
        t["palette"][k] = v.lstrip("#").upper()
    for role, face in t["fonts"].items():
        if face not in FONT_FILES:
            raise ThemeError(f"font {face!r} is not available in the sandbox (allowed: {sorted(FONT_FILES)})")
    return t


def rgb(hex6: str) -> tuple[int, int, int]:
    h = hex6.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mpl(hex6: str) -> str:
    return "#" + hex6.lstrip("#")
