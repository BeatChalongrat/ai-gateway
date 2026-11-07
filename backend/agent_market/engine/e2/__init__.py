"""E2 — structured extraction of Thai invoices and receipts (AM2).

    from agent_market.engine.e2 import extract
    out = extract(path, name, org={"tax_id": ...}, history=[...], today=date)

Schema: schema.py (``e2.extraction.v1``). Deterministic sources only
(e-Tax XML, PDF text layer, OCR); a model's reading enters as a candidate.
"""

from .extract import extract  # noqa: F401
from .schema import SCHEMA  # noqa: F401
