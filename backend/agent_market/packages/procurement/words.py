"""Every word this package says, in both of its languages.

Nothing in this package writes a sentence in its code. A sentence has a key;
its Thai is in the manifest under ``strings`` and its English under ``en``,
found by the Thai — the same place and the same rule as what the manifest
says before a run exists (AM11), so the one lint that fails a manifest string
without its English fails a question, a column heading or a line of the
purchase request too, and a string exists in both languages or in neither.

Customer data — what a person typed, what a page printed — is never looked up
here: it is put into a sentence as it is.
"""

from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _manifest() -> dict:
    try:
        from importlib.resources import files

        return json.loads(files(__package__).joinpath("agent.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — run from a checkout (the tests)
        with open(os.path.join(_HERE, "agent.json"), encoding="utf-8") as f:
            return json.load(f)


MANIFEST = _manifest()
STRINGS: dict[str, str] = MANIFEST.get("strings") or {}
ENGLISH: dict[str, str] = MANIFEST.get("en") or {}
LANGS = ("th", "en")


def lang_of(value) -> str:
    return "en" if str(value or "").lower() == "en" else "th"


def t(lang: str, key: str, **values) -> str:
    """The sentence ``key`` in ``lang``, with its {places} filled."""
    thai = STRINGS[key]
    text = ENGLISH[thai] if lang_of(lang) == "en" else thai
    return text.format(**values) if values else text


def both(key: str) -> dict:
    """A sentence in both languages (a heading a file is read back by)."""
    return {l: t(l, key) for l in LANGS}
