"""E3 — matching: two tables in, four groups out.

    ตรงกัน (matched) · ขาด (missing: in the left table, not in the right)
    เกิน (extra: in the right table, not in the left) · น่าสงสัย (suspect)

Every row keeps where it came from (file, sheet, row, cells). Matching is a
list of ordered passes in three tiers:

    exact        the rows share a key — the only tier a rule may accept by itself
    amount_date  the amounts and the dates agree within the install's tolerance
    fuzzy        the names or references resemble each other

Every pair below ``exact`` is a candidate: a person confirms it (a question
card through the clarification engine), and the pair records the pass, the
tier and that a person decided. No model adds a number or decides a pair;
the same two tables and the same answers give the same groups, the same pairs and the same questions.

    rows.py    Row, where it came from, and the keys its texts reduce to
    match.py   Tolerance, Pass, the passes, run() → the four groups
    cards.py   candidates and large unmatched rows → findings
"""

from .match import (  # noqa: F401
    DEFAULT_PASSES, MATCH, SEPARATE, TIERS, UNDECIDED, Pass, Proposal, Tolerance, best_pairs, pair_id, passes_by_id, run,
    tolerance_from,
)
from .rows import Row, name_key, ref_key, similarity  # noqa: F401
