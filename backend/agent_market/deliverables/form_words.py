"""What a filled form says on its face about what it lacks, in the shared words (AM22 §2.1).

A package that fills a customer's form (template_values) says what it knows cannot be known (``rows``) and
which of its fields this round has no concept of (``not_applicable``: {field: why}); the engine prints the
first under the heading on the form's face with every blank the filled file still holds empty
(engine/step.py), and takes the second off the face (template_out.fill). Every word is in the shared
engine's words, in both languages, said when it is read.
"""

from __future__ import annotations

from ..engine.words import SHARED as S


def gaps(rows=(), not_applicable: dict | None = None) -> dict:
    return {"title": S("form.gaps.title"), "lead": S("form.gaps.lead"), "columns": [S("form.gaps.col.what"), S("form.gaps.col.why")],
            "rows": [list(r) for r in rows], "form_blank": S("form.gaps.form_blank"), "form_why": {}, "sheet": S("form.gaps.title"),
            # the engine's check that all of this is on the file's face ({n}, {list}: filled in by the engine)
            "check": {"label": S("form.gaps.check.label"), "ok": S("form.gaps.check.ok", n="{n}"),
                      "missing": S("form.gaps.check.missing", list="{list}")},
            "not_applicable": dict(not_applicable or {})}
