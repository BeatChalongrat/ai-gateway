"""E2 questions → the existing clarification cards (engine/findings.py).

No second question mechanism: every E2 question becomes a ``Finding``, which
the runner's clarification engine (clarify.go) turns into the same cards AM1
asks with. Keys are per document and field (``e2_low_confidence:<doc>:<field>``)
so an answer can never land on another document's question.

The rule these cards enforce (AM2): a model may propose, a person decides.
Every financial card's *default* — what a skipped card records — is to leave
the document out as an exception, never to accept the number. A card that
takes a typed value says so (``input``); the typed words come back as
``{"typed": ...}`` and are checked by ``typed.parse`` before anything uses them.

AM5: what a skipped card records is not what the card *recommends*. A card
about a real purchase whose numbers do not add up (the VAT is not 7% of the
base) recommends recording it as printed, with an exception for whoever
reviews — leaving it out would lose the purchase. A card that needs the
person to look at the document (an unclear value, a missing one, the kind of
document, an invoice in another company's name) recommends nothing. Every
card whose skip leaves the document out says so (``leaves_out``), and the
document then goes on the round's "เอกสารที่ยังไม่ได้รวม" list.
"""

from __future__ import annotations

from collections.abc import Mapping

from .. import words
from ..findings import Finding, finding
from ..words import SHARED as S
from .schema import FIELD_KINDS

# Every table here keeps the keys of its words (engine/words.json) and says each in the language of the run
# when it is read: a table is made when this module is imported, before the step has said the language.
FIELDS = ("seller_name", "seller_tax_id", "seller_branch", "buyer_name", "buyer_tax_id", "document_number", "document_date", "due_date",
          "sum_lines", "discount", "subtotal", "vat_amount", "total", "wht_amount", "net_payable", "currency",
          "buyer_branch", "seller_address", "buyer_address", "vat_rate", "wht_rate", "amount_in_words", "bank_account")
# the name of a field in a sentence
FIELD_TH = S.table({f: f"e2.field.{f}" for f in FIELDS})
# … and on a card's heading: ≤ 12 display characters, in either language
HEADER = S.table({f: f"e2.header.{f}" for f in FIELDS}, then=words.cap)
DOC_TYPE_TH = S.table({k: f"e2.doc.{k}" for k in ("full_tax_invoice", "abbreviated_tax_invoice", "receipt", "invoice",
                                                  "credit_note", "debit_note")})
# where a value came from, as a person would say it (a source_conflict card names both readings)
SOURCE_TH = {"etax_xml": ("e2.source.etax", "e2.source.pdf_text")}



def reader_of(proposals: dict | None, doc_key: str) -> dict | None:
    """Which model read this document's page, and whether it runs in the organisation — as the runner
    wrote it beside the reading. ``None`` when no model read it."""
    p = (proposals or {}).get(doc_key)
    if not isinstance(p, dict) or not p.get("model"):
        return None
    return {"model": str(p["model"]), "self_hosted": p.get("self_hosted")}


def _reader(reader: dict | None) -> tuple[str, str]:
    """Who read the page, as a person would say it: (the one who read it, where the value came from).

    AM16: both said "the in-house model" whoever had read the page — also when an admin had let the
    agent's documents leave and a model outside read it, while the run's own card said the page had
    been sent out. The sentence says in-house only when the runner said so, names a model outside the
    organisation, and says neither when it was not told."""
    place = (reader or {}).get("self_hosted")
    model = str((reader or {}).get("model") or "")
    if place is True:
        return S("e2.reader.inside"), S("e2.source.model")
    if place is False and model:
        return S("e2.reader.outside", model=model), S("e2.source.model_outside", model=model)
    return S("e2.reader.unknown"), S("e2.source.model_unknown")
MATCH_TH = S.table({"document number": "e2.field.document_number", "date and total": "e2.match.date_total"})


class _Checks(Mapping):
    """A check that failed → (a card's heading, what is wrong), by the check's code."""

    KEYS = {
        "line_arithmetic": ("e2.check.line_arithmetic.head", "e2.check.line_arithmetic"),
        "subtotal_sum": ("e2.check.subtotal_sum.head", "e2.check.subtotal_sum"),
        "discount_arithmetic": ("e2.check.discount_arithmetic.head", "e2.check.discount_arithmetic"),
        "vat_7_percent": ("e2.check.vat_7_percent.head", "e2.check.vat_7_percent"),
        "grand_total": ("e2.header.total", "e2.check.grand_total"),
        "wht": ("e2.check.wht.head", "e2.check.wht"),
        "currency": ("e2.check.currency.head", "e2.check.currency"),
        "buyer_match": ("e2.check.buyer_match.head", "e2.check.buyer_match"),
        "seller_tax_id_format": ("e2.check.tax_id.head", "e2.check.seller_tax_id_format"),
        "buyer_tax_id_format": ("e2.check.tax_id.head", "e2.check.buyer_tax_id_format"),
        "document_date": ("e2.check.document_date.head", "e2.check.document_date"),
        "amount_in_words": ("e2.header.amount_in_words", "e2.check.amount_in_words"),
    }

    def __getitem__(self, code):
        head, why = self.KEYS[code]
        return words.cap(S(head)), S(why)

    def __iter__(self):
        return iter(self.KEYS)

    def __len__(self):
        return len(self.KEYS)


CHECK_TH = _Checks()
INPUT_KIND = {"money": "money", "tax_id": "tax_id", "date": "date", "code": "text", "party": "text", "text": "text",
              "rate": "rate", "bank_account": "text"}
INPUT_HINT = S.table({k: f"e2.hint.{k}" for k in ("money", "tax_id", "date", "rate", "text")})


def said(words_or_key) -> str:
    """Words an extraction keeps by key, said in the language of the run (a notice kept as words by an
    extraction made before AM14 is given as it is)."""
    return S(words_or_key) if words_or_key in S.strings else (words_or_key or "")


def exclude() -> tuple:
    """The answer every one of these cards has: leave the document out."""
    return S("e2.opt.exclude"), "exclude", S("e2.opt.exclude.d")


LEAVES_OUT = ("exclude", "reupload")
# failed checks of a real purchase: recording it as printed (with an exception) loses nothing.
# Not among them: an invoice in another company's name (it may not be this company's purchase)
# and a foreign currency (it needs a rate first).
RECORD_AS_PRINTED = ("line_arithmetic", "subtotal_sum", "discount_arithmetic", "vat_7_percent", "grand_total", "wht",
                     "seller_tax_id_format", "buyer_tax_id_format", "document_date", "amount_in_words")


def _doc_label(doc: dict) -> str:
    """How a card names a document: the seller as printed (only when it was read with certainty — an
    OCR'd or photographed name can be garbled), the document number, and always the file name."""
    f = doc.get("fields") or {}
    s = f.get("seller_name") or {}
    seller = None
    if s.get("value") and (s.get("confidence") or 0) >= 0.95 and not doc.get("document", {}).get("triage_only"):
        seller = " ".join(str(s["value"]).replace("\x00", "").split())
        if len(seller) > 40:
            seller = seller[:39] + "…"
    num = (f.get("document_number") or {}).get("normalized")
    name = doc.get("document", {}).get("name", "")
    parts = [p for p in (seller, S("e2.doc_no", num=num) if num else None) if p]
    return " ".join(parts) + f" ({name})" if parts else name


def _where(ev: dict) -> str:
    return " " + S("e2.where", page=ev["page"]) if ev.get("page") else ""


def _plain(v) -> str:
    """An expected/actual value from a check, as a person reads it."""
    if v is None:
        return ""
    try:
        from decimal import Decimal, InvalidOperation

        return f"{Decimal(str(v)):,.2f}" if str(v).replace(".", "", 1).replace("-", "", 1).isdigit() else str(v)
    except (InvalidOperation, ValueError):
        return str(v)


def _check_detail(code: str, ev: dict, reason: str) -> str:
    """What a failed check found, from its own numbers, in the words of the run's language (never the engine's
    own English message)."""
    import re

    exp, act = ev.get("expected"), ev.get("actual")

    def num(v) -> bool:
        return v is not None and not isinstance(v, (dict, list)) and str(v).replace(".", "", 1).replace("-", "", 1).isdigit()

    if code == "vat_7_percent" and num(act) and num(exp):
        return S("e2.detail.vat", act=_plain(act), exp=_plain(exp))
    if code == "grand_total" and num(act) and num(exp):
        return S("e2.detail.total", act=_plain(act), exp=_plain(exp))
    if code == "subtotal_sum" and num(act) and num(exp):
        return S("e2.detail.subtotal", exp=_plain(exp), act=_plain(act))
    if code == "buyer_match" and act:
        return S("e2.detail.buyer", act=act, exp=exp)
    if code in ("seller_tax_id_format", "buyer_tax_id_format") and act:
        return S("e2.detail.tax_id", act=act)
    if code == "document_date" and act:
        return S("e2.detail.date", act=act)
    if code == "currency" and act:
        return S("e2.detail.currency", act=act)
    if code == "amount_in_words" and act:
        return S("e2.detail.words", words=str(act)[:60])
    if code == "wht" and isinstance(act, dict):
        if "net" in act and "total" in act and "wht" not in act:
            return S("e2.detail.wht_unread", net=_plain(act["net"]), total=_plain(act["total"]))
        bits = [S("e2.detail.wht.withheld", wht=_plain(act.get("wht"))) if act.get("wht") is not None else "",
                S("e2.detail.wht.rate", rate=act["rate"]) if act.get("rate") is not None else "",
                S("e2.detail.wht.net", net=_plain(act.get("net"))) if act.get("net") is not None else S("e2.detail.wht.no_net")]
        return S("e2.detail.wht", bits=words.clauses(*bits))
    if code == "line_arithmetic":
        rows = re.findall(r"row (\d+): ([\d.,-]+) printed, ([\d.,-]+) computed", reason or "")
        if rows:
            return " · ".join(S("e2.detail.line", r=r, p=_plain(p), c=_plain(c)) for r, p, c in rows[:3])
    return ""


def _money(v) -> str:
    try:
        from decimal import Decimal

        return f"{Decimal(str(v)):,.2f}"
    except Exception:  # noqa: BLE001
        return str(v)


def _shown(kind: str, proposed):
    if proposed is None:
        return None
    if kind == "money":
        return _money(proposed)
    if kind == "party" and isinstance(proposed, dict):
        return proposed.get("core") or None
    return proposed


def findings_for(doc_key: str, doc: dict, reader: dict | None = None) -> list[Finding]:
    """Findings for one E2 extraction (``doc`` is the e2.extraction.v1 dict). ``reader``: the model that
    read the page (``reader_of``) — a question that shows a model's reading says which one it was."""
    out: list[Finding] = []
    label = _doc_label(doc)
    triage = doc.get("document", {}).get("triage_only")
    note = " " + S("e2.photo_note") if triage else ""
    for q in doc.get("questions") or []:
        code, subject, field = q["code"], q.get("subject") or "", q.get("field")
        subj = f"{doc_key}:{subject}" if subject else doc_key
        ev = {"document": doc.get("document", {}).get("name"), "question": q.get("reason"), **(q.get("evidence") or {})}
        if field in FIELD_KINDS:
            src = (doc.get("fields", {}).get(field) or {}).get("source") or {}
            ev["page"], ev["bbox"], ev["raw_text"] = src.get("page"), src.get("bbox"), src.get("raw_text")
        if code in ("low_confidence", "source_conflict") and field in FIELD_KINDS:
            kind = FIELD_KINDS[field]
            shown = _shown(kind, q.get("proposed"))
            what = FIELD_TH.get(field, field)
            this = S("e2.this.number") if kind in ("money", "tax_id", "rate", "bank_account") else S("e2.this.value")
            reason = str(q.get("reason") or "")
            other = _shown(kind, (q.get("evidence") or {}).get("other")) if code == "source_conflict" else None
            who, by_model = _reader(reader)
            pair = SOURCE_TH.get(doc.get("method"))
            first, second = (S(pair[0]), S(pair[1])) if pair else (S("e2.source.text"), by_model)
            where = _where(ev)
            if code == "source_conflict" and shown is not None and other is not None:
                text = S("e2.q.conflict", label=label, what=what, first=first, shown=shown, second=second, other=other,
                         where=where, note=note)
            elif code == "source_conflict":
                text = S("e2.q.conflict_unshown", label=label, what=what, where=where, note=note)
            elif shown is None and reason.startswith("the label is printed"):
                text = S("e2.q.label_only", label=label, what=what, where=where, note=note)
            elif shown is None:
                text = S("e2.q.unread", label=label, what=what, where=where, note=note)
            elif reason.startswith("only a model"):
                text = (S("e2.q.model_only", label=label, what=what, reader=who, shown=shown, where=where, note=note))
            elif reason.startswith("the numbers do not add up"):
                why = CHECK_TH.get(str((q.get("evidence") or {}).get("check") or ""), ("", S("e2.check.default")))[1]
                text = (S("e2.q.sums", label=label, what=what, shown=shown, why=why, where=where, note=note))
            else:
                text = S("e2.q.unsure", label=label, what=what, shown=shown, where=where, this=this, note=note)
            if shown is not None and other is not None and str(other) != str(shown):
                # two readings: the person picks the one the document shows (or types it)
                raw_other = (q.get("evidence") or {}).get("other")
                options = [(S("e2.opt.use", shown=shown)[:60], {"accept": shown if kind == "party" else str(q["proposed"])}, S("e2.opt.from", source=first)),
                           (S("e2.opt.use", shown=other)[:60], {"accept": other if kind == "party" else str(raw_other)}, S("e2.opt.from", source=second)),
                           exclude()]
            elif shown is not None:
                options = [(S("e2.opt.correct", shown=shown)[:60], {"accept": shown if kind == "party" else str(q["proposed"])},
                            S("e2.opt.correct.d", this=this)), exclude()]
            else:
                options = [(S("e2.opt.absent"), "absent", S("e2.opt.absent.d")), exclude()]
            out.append(finding(f"e2_{code}", "blocking", "preflight", HEADER.get(field, S("e2.header.value")), text,
                               options, "exclude", evidence=ev, subject=subj, leaves_out=True,
                               input={"kind": INPUT_KIND[kind], "hint": INPUT_HINT[INPUT_KIND[kind]]},
                               facts={"field": field, "proposed": shown}))
        elif code == "item_text":
            # AM8: a row whose text the item did not take in full — the person reads the page and decides
            e = q.get("evidence") or {}
            read, full = str(e.get("read") or ""), str(q.get("proposed") or "")
            left = " ".join(str(x) for x in e.get("left_out") or [])
            where = " " + S("e2.page", page=e["page"]) if e.get("page") else ""
            text = (S("e2.q.item_text", label=label, row=e.get("row"), read=read or "—", left=left, where=where, note=note))
            opts = [(S("e2.opt.full", full=full)[:60], {"accept": full}, S("e2.opt.full.d"))]
            if read:
                opts.append((S("e2.opt.as_read", read=read)[:60], {"accept": read}, S("e2.opt.as_read.d")))
            opts.append((S("e2.opt.unconfirmed"), "unconfirmed", S("e2.opt.unconfirmed.d")))
            out.append(finding("e2_item_text", "blocking", "preflight", S("e2.header.item_text"), text, opts, "unconfirmed",
                               evidence={**ev, "raw_text": e.get("raw_text"), "page": e.get("page")}, subject=subj,
                               input={"kind": "text", "hint": INPUT_HINT["text"]},
                               facts={"row": e.get("row"), "read": read, "proposed": full}))
        elif code == "field_missing" and field in FIELD_KINDS:
            kind = FIELD_KINDS[field]
            what = FIELD_TH.get(field, field)
            reason = str(q.get("reason") or "")
            dtype = DOC_TYPE_TH.get(str((doc.get("document_type") or {}).get("normalized") or ""))
            if reason.startswith("could not be read, and"):
                text = S("e2.q.missing_sums", label=label, what=what, note=note)
            elif reason.startswith("a ") and dtype:
                text = S("e2.q.missing_required", label=label, dtype=dtype, what=what, note=note)
            else:
                text = S("e2.q.missing", label=label, what=what, note=note)
            out.append(finding("e2_field_missing", "blocking", "preflight", HEADER.get(field, S("e2.header.missing")), text,
                               [(S("e2.opt.absent"), "absent", S("e2.opt.absent.exception")), exclude()], "exclude",
                               evidence=ev, subject=subj, leaves_out=True,
                               input={"kind": INPUT_KIND[kind], "hint": INPUT_HINT[INPUT_KIND[kind]]}, facts={"field": field}))
        elif code == "check_failed":
            head, why = CHECK_TH.get(subject, (S("e2.check.head"), S("e2.check.why")))
            detail = _check_detail(subject, q.get("evidence") or {}, str(q.get("reason") or ""))
            text = f"{label}: {why}" + (f" ({detail})" if detail else "")
            opts = [(S("e2.opt.as_printed"), "accept_as_printed",
                     S("e2.opt.as_printed.d")), exclude()]
            inp = None
            if subject == "currency":
                opts = [(S("e2.opt.rate"), "rate", S("e2.opt.rate.d")), exclude()]
                inp = {"kind": "rate", "hint": S("e2.hint.fx")}
            out.append(finding("e2_check_failed", "blocking", "preflight", head, text, opts, "exclude",
                               evidence=ev, subject=subj, input=inp, facts={"check": subject}, leaves_out=True,
                               recommended="accept_as_printed" if subject in RECORD_AS_PRINTED else None))
        elif code == "duplicate_candidate":
            m = (q.get("evidence") or {}).get("match") or {}
            if isinstance(m, dict) and m.get("name"):
                how = MATCH_TH.get(str(m.get("match")), S("e2.match.details"))
                text = (S("e2.q.duplicate_named", label=label, how=how, name=m["name"]))
            else:
                text = S("e2.q.duplicate", label=label)
            out.append(finding("e2_duplicate", "blocking", "preflight", S("e2.header.duplicate"), text,
                               [(S("e2.opt.dup_exclude"), "exclude", S("e2.opt.dup_exclude.d")),
                                (S("e2.opt.dup_include"), "include", S("e2.opt.dup_include.d"))],
                               # the same purchase is already in the register: leaving the copy out loses nothing
                               "exclude", evidence=ev, subject=subj, leaves_out=True, recommended="exclude"))
        elif code == "copy_not_original":
            # blocking: whether a copy is recorded changes which documents count (a cosmetic answer never
            # reaches the numbers); skipped → left out, like every other E2 card
            out.append(finding("e2_copy", "blocking", "preflight", S("e2.header.copy"),
                               S("e2.q.copy", label=label),
                               [(S("e2.opt.copy_ok"), "ok", S("e2.opt.copy_ok.d")), exclude()], "exclude",
                               evidence=ev, subject=subj, leaves_out=True, recommended="ok"))
        elif code == "document_type_unknown":
            out.append(finding("e2_doc_type", "blocking", "preflight", S("e2.header.doc_type"),
                               S("e2.q.doc_type", label=label),
                               [(words.cap(DOC_TYPE_TH["full_tax_invoice"]), "full_tax_invoice", S("e2.doc.full.d")),
                                (words.cap(DOC_TYPE_TH["abbreviated_tax_invoice"]), "abbreviated_tax_invoice", S("e2.doc.abbreviated.d")),
                                (words.cap(DOC_TYPE_TH["receipt"]), "receipt", S("e2.doc.receipt.d")), exclude()],
                               "exclude", evidence=ev, subject=subj, leaves_out=True))
        elif code == "page_quality":
            out.append(finding("e2_page_quality", "blocking", "preflight", S("e2.header.page_quality"),
                               S("e2.q.page_quality", label=label, page=subject[1:]),
                               [(S("e2.opt.reupload"), "reupload", S("e2.opt.reupload.d")), exclude()],
                               "exclude", evidence=ev, subject=subj, leaves_out=True))
    return out


def answer_for(answers: dict, f: Finding):
    """The answer the person gave to one of these findings (by key), or None."""
    return answers.get(f.key)
