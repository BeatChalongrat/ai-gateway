"""A person's answers to the E2 cards → what judging a document again needs.

AM8: the rules live in the engine (engine/e2/decisions.py), as AM6 announced
for this package's next version: one set of answer rules for every agent that
reads documents through E2, the item-name card included. This module keeps its
name for the code that imports it.
"""

from __future__ import annotations

from ....engine.e2.decisions import (  # noqa: F401 — re-exported
    BRING_BACK,
    DOC_TYPES,
    EXCLUDED_BY,
    PRIORITY,
    Decisions,
    for_document,
    restore_of,
    split_key,
    to_value,
)
