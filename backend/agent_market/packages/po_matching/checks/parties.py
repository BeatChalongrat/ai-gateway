"""Who the supplier is.

The 13-digit tax ID is the key: two documents with the same tax ID are the same supplier however
the name is written (บริษัท เอเชีย ซัพพลาย จำกัด · บจก.เอเชียซัพพลาย · ASIA SUPPLY CO., LTD.), and two
with different tax IDs are different suppliers however alike the names are.

Only when a tax ID is missing on one side are the names compared, without their legal form. Names
that are the same then count as the same supplier, and the result says it was the name that
decided. A company and a partnership of the same name are two legal persons: that, and names that
only resemble each other, is for a person to decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ....engine.e2 import thai_text as T
from ....engine.e3.rows import name_key, similarity
from ..words import W

SAME, DIFFERENT, UNSURE = "same", "different", "unsure"
NEAR = Decimal("0.85")
HOW_TH = W.table({
    "tax_id": "how.tax_id",
    "tax_id_differs": "how.tax_id_differs",
    "name": "how.name",
    "legal_form": "how.legal_form",
    "name_near": "how.name_near",
    "name_differs": "how.name_differs",
    "unknown": "how.unknown",
})


@dataclass(frozen=True)
class Party:
    name: str = ""
    tax_id: str = ""
    branch: str = ""

    @property
    def valid_tax_id(self) -> bool:
        return len(self.tax_id) == 13 and self.tax_id.isdigit()

    @property
    def key(self) -> str:
        """What rows of one supplier share: the tax ID, else the name without its legal form."""
        return self.tax_id if self.valid_tax_id else ("n:" + name_key(self.name) if name_key(self.name) else "")

    def out(self) -> dict:
        return {"name": self.name, "tax_id": self.tax_id, "branch": self.branch}


def _form(name: str) -> str:
    f = T.legal_form(name or "")
    return f["legal_form"] if f else ""


def compare(a: Party, b: Party) -> tuple[str, str]:
    """(same | different | unsure, how it was decided)."""
    if a.valid_tax_id and b.valid_tax_id:
        return (SAME, "tax_id") if a.tax_id == b.tax_id else (DIFFERENT, "tax_id_differs")
    ka, kb = name_key(a.name), name_key(b.name)
    if not ka or not kb:
        return UNSURE, "unknown"
    if ka == kb:
        fa, fb = _form(a.name), _form(b.name)
        if fa and fb and fa != fb:
            return UNSURE, "legal_form"
        return SAME, "name"
    if similarity(a.name, b.name) >= NEAR:
        return UNSURE, "name_near"
    return DIFFERENT, "name_differs"
