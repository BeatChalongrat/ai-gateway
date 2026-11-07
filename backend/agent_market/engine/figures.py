"""The figures a commentary may name (AM22 §1.1; AM8: by key, each with its own citation).

A sentence a model writes names a figure as ``{key}``; the sheet prints the figure's text and its
citation there. Only a figure that has a citation goes in the table: a number nobody can point to is not
one a sentence may name. A count under a figure's key is printed as the count; money as the package
prints money. The runner's guard (deliver.go guardFigures) refuses a sentence that types a number or
names a key that is not here.
"""

from __future__ import annotations

from .words import SHARED as S


def table(spec) -> dict:
    """``spec``: (key, printed text, citation id, label) rows → {key: {"text", "cite", "label"}}; a row with no
    text or no citation is left out."""
    out: dict = {}
    for key, text, cite, label in spec:
        if text in (None, "") or not cite:
            continue
        out[key] = {"text": str(text), "cite": cite, "label": label}
    return out


def facts(*blocks) -> list:
    """``blocks``: (heading, [lines]) → [{"heading", "lines"}], empty blocks left out."""
    return [{"heading": h, "lines": [l for l in lines if l]} for h, lines in blocks if any(lines)]


def capped(lines: list, limit: int, more) -> list:
    """The rules' list under one heading, at most ``limit`` points: when there are more, the last point says
    how many more there are (``more(n)``) — every one of them is on another sheet of the same file. Nothing is
    left out without a line that says so."""
    lines = [l for l in lines if l]
    if limit <= 0 or len(lines) <= limit:
        return lines
    keep = lines[: limit - 1]
    return keep + [more(len(lines) - len(keep))]


def capped_rules(lines: list) -> list:
    """AM22: the rules' list under one heading of the commentary sheet — at most the sheet's ceiling, and when
    there are more, a last point that says how many (every one of them is on another sheet of the file)."""
    from ..deliverables.look.catalogue import writing_limits

    return capped(list(lines), writing_limits("xlsx")["items_max"], lambda n: S("doc.commentary.more", n=n))
