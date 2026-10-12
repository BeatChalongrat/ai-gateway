"""Every document of a run judged in the run's context — the one place preflight and compute share.

Context, applied the same way on every pass:
  * the install's company (tax ID, branch) — a full tax invoice made out to another buyer fails;
  * the duplicate index — documents recorded by earlier runs of this install, plus the documents
    before this one in this run (a batch can hold the same invoice twice);
  * a vision model's reading of a scan or photo — a candidate only (engine/e2/extract.judge);
  * the person's answers to the cards (checks/decisions.py).

A document ends a pass as ``included``, ``excluded`` (with the reason code) or ``pending`` (it
still has questions). Compute never records a pending document: whatever is still open there is
left out as ``unresolved_question`` — a number is never accepted without a decision.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from ....engine.e2 import cards
from ....engine.e2.extract import rejudge
from ....engine.e2.schema import FIELD_KINDS
from ....engine.findings import Finding, finding
from ..words import W
from . import decisions as D

SCAN_TIERS = ("scan", "photo")


@dataclass
class Doc:
    """One parsed upload: the E2 extraction as the parse step read it."""
    key: str
    name: str
    sha256: str
    extraction: dict

    @property
    def tier(self) -> str:
        return self.extraction["document"]["tier"]

    def summary(self) -> dict:
        x = self.extraction
        return {"doc_key": self.key, "name": self.name, "tier": self.tier, "method": x["method"],
                "pages": x["document"]["pages"], "document_type": x["document_type"]["normalized"],
                "questions": len(x["questions"]), "triage_only": x["document"]["triage_only"]}


@dataclass
class Reviewed:
    doc: Doc
    out: dict
    dec: D.Decisions
    status: str                       # included | excluded | pending
    exclusion: dict | None = None     # {code, detail, decided_by}
    findings: list = field(default_factory=list)


def org_from(defaults: dict) -> dict | None:
    tid = "".join(ch for ch in str((defaults or {}).get("company_tax_id") or "") if ch.isdigit())
    if len(tid) != 13:
        return None
    return {"tax_id": tid, "branch": (defaults or {}).get("company_branch") or None,
            "name": (defaults or {}).get("company") or None}


def _date_of(doc: Doc):
    v = ((doc.extraction.get("fields") or {}).get("document_date") or {}).get("normalized")
    return str(v) if v else "9999-12-31"


_COPY_RANK = {"original": 0, "unmarked": 1, "copy": 2}
_TIER_RANK = {"etax": 0, "digital": 1, "scan": 2, "photo": 3}


def _review_order(doc: Doc) -> tuple:
    """The order documents are judged in, by content (AM4): date; then, between two copies of one
    document, the original before one marked a copy and a clearer file before a worse one — so the
    one recorded is the better record — then the bytes' hash (the key). Never a name or upload order."""
    d = doc.extraction.get("document") or {}
    return (_date_of(doc), _COPY_RANK.get(d.get("copy_mark"), 1), _TIER_RANK.get(d.get("tier"), 9), doc.key)


def index_entry(doc: Doc, out: dict) -> dict:
    f = out["fields"]

    def v(n):
        return (f.get(n) or {}).get("normalized")
    return {"name": doc.name, "sha256": doc.sha256, "doc_key": doc.key, "seller_tax_id": v("seller_tax_id"),
            "document_number": v("document_number"), "document_date": v("document_date"), "total": v("total")}


def proposals_for(doc: Doc, proposals: dict | None) -> list[dict]:
    """A vision reading is used for scans and photos only: digital and e-Tax documents are read exactly."""
    p = (proposals or {}).get(doc.key)
    if not p or doc.tier not in SCAN_TIERS or not isinstance(p.get("fields"), dict):
        return []
    return [{"method": f"vision:{p.get('model') or 'model'}", "fields": p["fields"]}]


def _invalid_cards(doc: Doc, out: dict, dec: D.Decisions) -> list[Finding]:
    fs = []
    label = cards._doc_label(out)
    for name, (key, why) in sorted(dec.invalid.items()):
        if name == "fx_rate":
            what, kind, hint = W("field.fx_rate"), "rate", W("hint.fx")
            opts = [cards.exclude(), (W("opt.no_rate"), "reupload", W("opt.no_rate.d"))]
            default = "exclude"
        else:
            what = cards.FIELD_TH.get(name, name)
            kind = cards.INPUT_KIND.get(FIELD_KINDS.get(name, "text"), "text")
            hint = cards.INPUT_HINT.get(kind, cards.INPUT_HINT["text"])
            opts = [(W("opt.absent"), "absent", W("opt.absent.d")), cards.exclude()]
            default = "exclude"
        fs.append(finding("e2_typed_invalid", "blocking", "preflight", W("card.retype.header"),
                          W("card.retype.question", label=label, what=what, why=why),
                          opts, default, subject=f"{doc.key}:{name}", input={"kind": kind, "hint": hint},
                          evidence={"document": doc.name, "answered_by": key}, facts={"field": name}))
    return fs


def review(docs: list[Doc], *, answers: dict | None, defaults: dict | None, today: _dt.date | None,
           history: list[dict] | None = None, proposals: dict | None = None) -> list[Reviewed]:
    org = org_from(defaults or {})
    hist = [h for h in (history or []) if isinstance(h, dict)]
    seen: dict[str, str] = {}
    res: list[Reviewed] = []
    # which of two copies is kept follows what they are, never their names or upload order (AM4)
    for doc in sorted(docs, key=_review_order):
        dec = D.for_document(doc.key, answers or {})
        if doc.sha256 in seen:
            res.append(Reviewed(doc, doc.extraction, dec, "excluded",
                                {"code": "same_file_twice", "detail": W("left.same_file", name=seen[doc.sha256]),
                                 "decided_by": "rule", "restore": None}))
            continue
        seen[doc.sha256] = doc.name
        out = rejudge(doc.extraction, org=org, history=hist, today=today, proposals=proposals_for(doc, proposals),
                      **dec.judge_kwargs(doc.extraction.get("fields") or {}))
        if dec.excluded:
            who = cards.reader_of(proposals, doc.key)
            asked = cards.findings_for(doc.key, doc.extraction, who) + cards.findings_for(doc.key, out, who)
            f = next((x for x in asked if x.key == dec.excluded["key"]), None)
            # the card's question as the person read it (evidence["question"] is the engine's English reason)
            detail = (f.question if f else "") or ""
            if dec.excluded.get("reupload"):
                detail = (detail + " · " + W("left.reupload")).strip(" ·")
            res.append(Reviewed(doc, out, dec, "excluded",
                                {"code": dec.excluded["code"], "codes": dec.excluded.get("codes", [dec.excluded["code"]]),
                                 "detail": detail, "decided_by": "card", "subject": dec.excluded.get("subject"),
                                 "reupload": dec.excluded.get("reupload", False), "restore": D.restore_of(dec)}))
            continue
        fs = cards.findings_for(doc.key, out, cards.reader_of(proposals, doc.key)) + _invalid_cards(doc, out, dec)
        res.append(Reviewed(doc, out, dec, "pending" if fs else "included", findings=fs))
        hist.append(index_entry(doc, out))
    return res


def period_of(reviewed: list[Reviewed], today: _dt.date | None) -> tuple[str, str]:
    """The month most of the recorded documents are dated in (ties: the latest), else today's."""
    months: dict[str, int] = {}
    for r in reviewed:
        if r.status == "excluded":
            continue
        d = (r.out["fields"].get("document_date") or {}).get("normalized")
        if d:
            months[str(d)[:7]] = months.get(str(d)[:7], 0) + 1
    if months:
        return max(months, key=lambda m: (months[m], m)), "documents"
    t = today or _dt.date.today()
    return f"{t.year:04d}-{t.month:02d}", "today"
