"""E2 extraction: one document in, the ``e2.extraction.v1`` contract out.

    extract(path, name, org=..., history=..., today=..., proposals=..., ocr=...)

Sources, strongest first: the ETDA XML of an e-Tax document (embedded in the
PDF or alone) → the PDF text layer (with the Thai marks it lost put back
where the document proves them, marks.py) → OCR of the page image (scans at
300 DPI, photos deskewed and upscaled). A model's reading ("proposals",
e.g. the vision fallback) is only ever a *candidate*: it can raise the
confidence of a value a deterministic source also read, or become the
suggested answer of a question — it never fills a financial field by itself.

Confidence starts from the source (the text layer and XML ≈ 1.0; OCR = the
lowest word confidence of the value) and rises only when independent
numbers corroborate each other (7 % of the base is the VAT, base + VAT is
the total, the lines sum to the base, the amount in words is the total, the
tax ID's check digit holds). Questions follow the AM2 card's thresholds.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
from decimal import Decimal

from . import checks as C
from . import etax, marks, parse
from . import thai_text as T
from dataclasses import asdict, dataclass

from .schema import (FIELD_KINDS, MONEY_FIELDS, ORDINARY_THRESHOLD, PAGE_OCR_THRESHOLD, REQUIRED, SCHEMA,
                     STRICT_KINDS, STRICT_THRESHOLD, TRIAGE_FIELDS, Field, Question, Source, validate)
from .words import Page, build_lines, ocr_words_from, pdf_text_words

MIN_TEXT_CHARS = 24          # a page with less text than this is a scan (extract_text.MIN_TEXT_CHARS_PER_PAGE)
OCR_DPI = 300
CORROBORATED = 0.97


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_ocr():
    import extract_text  # the sandbox's /sandbox/scripts (services/chat-edge-api/scripts)

    return extract_text.ocr_image_words, extract_text.render_page


def _restore_marks(path: str, words: list, fonts: dict, ocr) -> None:
    """Thai marks the text layer lost, put back where the document proves them (marks.py). Without
    Tesseract, or on any failure, the words stay as the layer wrote them."""
    if not any(w.lost for w in words):
        return
    try:
        ocr_fn, render = ocr or _default_ocr()
    except Exception:  # noqa: BLE001 — no OCR in this environment: the font alone
        ocr_fn, render = None, None
    try:
        marks.restore(path, words, fonts, ocr_fn, render)
    except Exception:  # noqa: BLE001 — a repair that fails leaves the text layer as it was
        pass


def _read_pdf(path: str, ocr) -> tuple[list, list[Page], str]:
    fonts: dict = {}
    words, pages = pdf_text_words(path, fonts)
    by_page: dict[int, int] = {}
    for w in words:
        by_page[w.page] = by_page.get(w.page, 0) + len(w.text)
    scanned = [p for p in pages if by_page.get(p.number, 0) < MIN_TEXT_CHARS]
    _restore_marks(path, [w for w in words if w.page not in {p.number for p in scanned}], fonts, ocr)
    if not scanned:
        return words, pages, "pdf_text"
    ocr_fn, render = ocr or _default_ocr()
    from .image import best_ocr, prepare

    words = [w for w in words if w.page not in {p.number for p in scanned}]
    for p in scanned:
        img = render(path, p.number - 1, OCR_DPI)
        img, tf = prepare(img, photo=False)
        res, mode = best_ocr(img, ocr_fn, photo=False)
        scale = 72.0 / OCR_DPI
        words += ocr_words_from(res, p.number, scale=scale)
        confs = [w["conf"] for w in res["words"] if w["conf"] >= 0]
        p.method, p.dpi = "ocr", OCR_DPI
        p.ocr_conf = round(sum(confs) / len(confs), 1) if confs else 0.0
        p.width, p.height = res["width"] * scale, res["height"] * scale
    method = "ocr" if len(scanned) == len(pages) else "hybrid"
    return words, pages, method


def _read_image(path: str, ocr) -> tuple[list, list[Page], dict]:
    from PIL import Image

    from .image import best_ocr, prepare

    ocr_fn, _ = ocr or _default_ocr()
    img, tf = prepare(Image.open(path), photo=True)
    res, mode = best_ocr(img, ocr_fn, photo=True)
    confs = [w["conf"] for w in res["words"] if w["conf"] >= 0]
    page = Page(number=1, width=float(res["width"]), height=float(res["height"]), unit="px", method="ocr",
                ocr_conf=round(sum(confs) / len(confs), 1) if confs else 0.0)
    return ocr_words_from(res, 1, scale=1.0), [page], {**tf, "psm": mode}


@dataclass
class State:
    """What reading a document produced, before any judgement: stored with the extraction
    (``out["state"]``) so a later step can judge it again with the run's duplicate history,
    a model's proposals or a person's answers, without reading the file again."""
    fields: dict
    items: list
    doc_type: Field
    pages: list
    name: str
    sha256: str
    kind: str
    tier: str
    method: str
    transform: dict | None
    copy_mark: str
    vat_mode: str
    agreement: dict
    has_xml: bool


def extract(path: str, name: str | None = None, *, org: dict | None = None, history: list[dict] | None = None,
            today: _dt.date | None = None, proposals: list[dict] | None = None, ocr=None) -> dict:
    """Read one document and judge it (the parse step's whole job, and the eval's)."""
    return judge(read(path, name, ocr=ocr), org=org, history=history, today=today, proposals=proposals)


def read(path: str, name: str | None = None, *, ocr=None) -> State:
    name = name or os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    fields: dict[str, Field] = {}
    items: list[dict] = []
    agreement: dict[str, dict] = {}
    tier, method, kind = "digital", "pdf_text", "pdf"
    pages: list[Page] = []
    transform = None
    doc_type = Field()
    copy_mark = "unmarked"
    lines = []

    xml = None
    if ext == ".xml":
        kind, tier = "xml", "etax"
        with open(path, "rb") as f:
            xml = (name, f.read())
    elif ext == ".pdf":
        emb = etax.embedded_xml(path)
        if emb and etax.is_etda(emb[1]):
            xml, tier = emb, "etax"
    if ext in (".jpg", ".jpeg", ".png", ".heic", ".webp"):
        kind, tier = "image", "photo"
        words, pages, transform = _read_image(path, ocr)
        method = "ocr"
    elif ext == ".pdf":
        words, pages, method = _read_pdf(path, ocr)
        if method in ("ocr", "hybrid") and tier != "etax":
            tier = "scan"
    else:
        words = []

    if words:
        lines = build_lines(words)
        items, table = parse.line_items(lines)
        table_top = None
        for l in lines:
            if parse._header_cols(l):
                table_top = l.y0
                break
        breg, bseg = parse.buyer_region(lines, pages, table_top)
        doc_type = parse.document_type(lines, pages)
        seller, buyer = parse.party_names(lines, pages, breg, bseg)
        stid, btid, _all_ids = parse.tax_ids(lines, breg)
        sbr, bbr = parse.branches(lines, stid, btid, breg)
        doc_date, due = parse.dates(lines)
        fields.update({"seller_name": seller, "buyer_name": buyer, "seller_tax_id": stid, "buyer_tax_id": btid,
                       "seller_branch": sbr, "buyer_branch": bbr, "document_number": parse.document_number(lines),
                       "document_date": doc_date, "due_date": due, "amount_in_words": parse.amount_in_words(lines),
                       "bank_account": parse.bank_account(lines)})
        fields.update(parse.totals(lines, pages))
        copy_mark = parse.original_or_copy(lines, pages)
        cur = T.detect_currency(" ".join(l.text for l in lines))
        fields["currency"] = Field(value=cur, normalized=cur, confidence=1.0 if tier == "digital" else 0.9,
                                   source=None)
        if fields["currency"].source is None:
            fields["currency"].candidates = [{"value": cur, "normalized": cur, "method": "document_scan", "confidence": 1.0}]

    if xml is not None:
        x = etax.parse(xml[1], xml[0])
        text_fields = fields
        fields = dict(text_fields)
        for k, f in x["fields"].items():
            fields[k] = f
        if x["line_items"]:
            items = x["line_items"]
        doc_type = x["document_type"] if x["document_type"].normalized != "unknown" else doc_type
        method = "etax_xml"
        # the text layer (when there is one) must agree with the XML on what matters
        for k in ("document_number", "seller_tax_id", "buyer_tax_id", "total", "vat_amount", "document_date"):
            a, b = x["fields"].get(k), text_fields.get(k)
            if a is not None and b is not None and b.present and a.present:
                agree = str(a.normalized) == str(b.normalized)
                agreement[k] = {"agree": agree, "primary": a.normalized, "other": b.normalized,
                                "message": "" if agree else "the PDF text says something else than its e-Tax XML"}

    # derived: no discount and no separate base → the line total is the base
    if not _present(fields, "subtotal") and _present(fields, "sum_lines") and not _present(fields, "discount"):
        s = fields["sum_lines"]
        fields["subtotal"] = Field(value=s.value, normalized=s.normalized, confidence=s.confidence, source=s.source,
                                   checks=["derived_from_sum_lines"])
    vmode = parse.vat_mode(lines, fields) if lines else ("exclusive" if _present(fields, "vat_amount") else "none")
    # prices that include VAT without saying so (a POS slip's VATABLE / VAT / TOTAL): the lines add up to the
    # total, and base + VAT is the total
    line_sum = sum((it["amount"].normalized for it in items if it.get("amount") is not None and it["amount"].present), Decimal("0"))
    if vmode == "exclusive" and items and all(_present(fields, n) for n in ("subtotal", "vat_amount", "total")):
        sub, vat, tot = (fields[n].normalized for n in ("subtotal", "vat_amount", "total"))
        if abs(line_sum - tot) <= Decimal("0.01") and abs(sub + vat - tot) <= Decimal("0.01") and abs(sub - line_sum) > Decimal("0.01"):
            vmode = "inclusive"
    # computed, never read: the line total when the document does not print one, and — with no
    # VAT on the document — the base is the total. Marked as computed; a check still covers it.
    amounts = [it["amount"] for it in items if it.get("amount") is not None and it["amount"].present]
    if not _present(fields, "sum_lines") and amounts:
        v = sum((a.normalized for a in amounts), Decimal("0"))
        fields["sum_lines"] = Field(value=f"{v:.2f}", normalized=T.q2(v), confidence=min(a.confidence for a in amounts),
                                    candidates=[{"value": f"{v:.2f}", "normalized": T.q2(v), "method": "computed:sum_of_lines",
                                                 "confidence": min(a.confidence for a in amounts)}],
                                    checks=["computed_from_lines"])
    if vmode == "none" and _present(fields, "total"):
        t = fields["total"]
        for n in ("subtotal", "sum_lines"):
            if not _present(fields, n):
                fields[n] = Field(value=t.value, normalized=t.normalized, confidence=t.confidence, source=t.source,
                                  checks=["no_vat_base_is_total"])
    if vmode == "exclusive" and not _present(fields, "vat_rate"):
        fields["vat_rate"] = Field(value="7", normalized="7", confidence=0.9, source=None,
                                   candidates=[{"value": "7", "normalized": "7", "method": "default", "confidence": 0.9}])
    return State(fields=fields, items=items, doc_type=doc_type, pages=pages, name=name, sha256=_sha(path), kind=kind,
                 tier=tier, method=method, transform=transform, copy_mark=copy_mark, vat_mode=vmode, agreement=agreement,
                 has_xml=xml is not None)


def judge(state: State, *, org: dict | None = None, history: list[dict] | None = None, today: _dt.date | None = None,
          proposals: list[dict] | None = None, overrides: dict | None = None, confirmed: set | None = None,
          absent: set | None = None, accepted_checks: set | None = None, doc_type: str | None = None,
          override_methods: dict | None = None) -> dict:
    """Checks, confidence and questions for a read document.

    A person's answers come back as: ``overrides`` {field: typed value (normalised)}, ``confirmed``
    (fields the person said are right as read), ``absent`` (required fields the person confirmed the
    document does not print — recorded by the package as an exception, not asked again),
    ``accepted_checks`` (failed checks the person accepted as printed — likewise) and ``doc_type``
    (the type the person chose). ``override_methods`` names how an override came about
    (default ``typed_by_person``; ``confirmed_by_person`` for a proposal the person accepted).
    A field the person typed or confirmed is no longer in dispute: its source conflict is dropped."""
    import copy

    today = today or _dt.date.today()
    fields = copy.deepcopy(state.fields)
    items = copy.deepcopy(state.items)
    pages, vmode, copy_mark, tier, method = state.pages, state.vat_mode, state.copy_mark, state.tier, state.method
    decided = {n for n in (overrides or {}) if n in FIELD_KINDS} | set(confirmed or ())
    agreement = {k: v for k, v in state.agreement.items() if k not in decided}
    doc_type = Field(value=doc_type, normalized=doc_type, confidence=1.0,
                     source=Source(page=None, bbox=None, span=None, method="chosen_by_person", raw_text=doc_type)) \
        if doc_type else copy.deepcopy(state.doc_type)
    for n, v in (overrides or {}).items():
        if n in FIELD_KINDS and v is not None:
            how = (override_methods or {}).get(n, "typed_by_person")
            shown = v.get("core", str(v)) if isinstance(v, dict) else str(v)
            fields[n] = Field(value=shown, normalized=v, confidence=1.0,
                              source=Source(page=None, bbox=None, span=None, method=how, raw_text=shown),
                              checks=[how])
    for n in confirmed or ():
        if n in fields and fields[n].present:
            fields[n].confidence = 1.0
            fields[n].checks.append("confirmed_by_person")

    triage = tier == "photo"
    dtype = doc_type.normalized or "unknown"

    # model proposals: candidates only
    conflicts: dict[str, dict] = {}
    for prop in proposals or []:
        for k, v in (prop.get("fields") or {}).items():
            if k not in FIELD_KINDS or v in (None, ""):
                continue
            norm = _normalise(k, v)
            if norm is None:
                continue
            f = fields.setdefault(k, Field())
            cand = {"value": v, "normalized": norm, "method": prop.get("method", "model"), "confidence": 0.8}
            f.candidates.append(cand)
            if f.present:
                if k in decided:
                    continue          # the person already decided this field
                if _same_value(k, f.normalized, norm):
                    f.checks.append("model_agrees")
                    f.confidence = max(f.confidence, CORROBORATED if f.confidence >= 0.6 else f.confidence)
                else:
                    conflicts[k] = {"agree": False, "primary": f.normalized, "other": norm,
                                    "message": f"{prop.get('method', 'the model')} read {norm}, the document text {f.normalized}"}

    checks = C.run(fields, items, doc_type=dtype, vat_mode=vmode, org=org, history=history, copy_mark=copy_mark,
                   today=today, agreement={**agreement, **conflicts})
    by = {c.code: c for c in checks}

    # corroboration: numbers read separately that agree with each other are trusted
    def boost(names, why):
        for n in names:
            f = fields.get(n)
            if f is not None and f.present and f.confidence >= 0.5 and (f.source is not None):
                f.confidence = max(f.confidence, CORROBORATED)
                f.checks.append(why)
    if by["vat_7_percent"].status == "pass" and by["grand_total"].status == "pass":
        boost(("subtotal", "vat_amount", "total", "vat_rate"), "vat_and_total_agree")
    if by["subtotal_sum"].status == "pass":
        boost(("sum_lines", "subtotal") if vmode != "inclusive" else ("total",), "lines_sum_to_base")
        for it in items:
            for k in ("amount",):
                f = it.get(k)
                if f is not None and f.present and f.confidence >= 0.5:
                    f.confidence = max(f.confidence, CORROBORATED)
    if by["line_arithmetic"].status == "pass":
        for it in items:
            for k in ("quantity", "unit_price", "amount"):
                f = it.get(k)
                if f is not None and f.present and f.confidence >= 0.5:
                    f.confidence = max(f.confidence, CORROBORATED)
    if by["wht"].status == "pass":
        boost(("wht_amount", "net_payable", "wht_rate"), "wht_consistent")
    if by["amount_in_words"].status == "pass":
        boost(("total", "net_payable"), "amount_in_words_agrees")
    if by["discount_arithmetic"].status == "pass" if "discount_arithmetic" in by else False:
        boost(("discount", "sum_lines"), "discount_consistent")
    for who in ("seller", "buyer"):
        c = by[f"{who}_tax_id_format"]
        f = fields.get(f"{who}_tax_id")
        if c.status == "pass" and f is not None and f.confidence >= 0.6:
            f.confidence = max(f.confidence, 0.96)
            f.checks.append("check_digit_ok")
    for c in checks:
        if c.status == "pass":
            for n in c.fields:
                if n in fields and fields[n].present and c.code not in fields[n].checks:
                    fields[n].checks.append(c.code)

    questions = _questions(fields, items, checks, pages, dtype, triage, absent=set(absent or ()),
                           accepted=set(accepted_checks or ()), ocr=tier in ("scan", "photo"))

    scope = TRIAGE_FIELDS if triage else tuple(FIELD_KINDS)
    out = {
        "schema": SCHEMA,
        "document": {"name": state.name, "sha256": state.sha256, "pages": len(pages) or (1 if state.has_xml else 0),
                     "kind": state.kind, "tier": tier, "triage_only": triage,
                     "page_quality": [{"page": p.number, "method": p.method, "ocr_confidence": p.ocr_conf, "dpi": p.dpi,
                                       "unit": p.unit, "width": round(p.width, 1), "height": round(p.height, 1)} for p in pages],
                     "image_transform": state.transform, "copy_mark": copy_mark, "vat_mode": vmode,
                     "notice": ("รูปถ่ายใบเสร็จอ่านได้แบบพยายามที่สุดเท่านั้น (best-effort): ระบบอ่านแค่ผู้ขาย วันที่ ยอดรวม VAT "
                                "และเลขผู้เสียภาษี และจะถามกลับทุกครั้งที่ไม่มั่นใจ") if triage else None},
        "document_type": doc_type.to_dict(),
        "method": method,
        "fields": {k: fields.get(k, Field()).to_dict() for k in FIELD_KINDS if k in scope},
        "line_items": [] if triage else [{"row": it["row"], "page": it["page"],
                                          **{k: (it.get(k) or Field()).to_dict() for k in ("description", "quantity", "unit_price", "amount")}}
                                         for it in items],
        "deterministic_checks": [c.to_dict() for c in checks],
        "questions": [q.to_dict() for q in questions],
        "state": state_dict(state),
    }
    validate(out)
    return out


# ── the stored state ────────────────────────────────────────────────────────
# Exact types survive the run's JSON: Decimal and date are tagged, never guessed from a field's kind.

def _enc(v):
    if isinstance(v, Decimal):
        return {"$dec": str(v)}
    if isinstance(v, _dt.date):
        return {"$date": v.isoformat()}
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    return v


def _dec(v):
    if isinstance(v, dict):
        if set(v) == {"$dec"}:
            return Decimal(v["$dec"])
        if set(v) == {"$date"}:
            return _dt.date.fromisoformat(v["$date"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def _field_load(d: dict) -> Field:
    d = _dec(d)
    src = d.pop("source", None)
    return Field(**d, source=Source(**src) if src else None)


def state_dict(s: State) -> dict:
    return {"fields": {k: _enc(asdict(f)) for k, f in s.fields.items()},
            "items": [{k: (_enc(asdict(v)) if isinstance(v, Field) else _enc(v)) for k, v in it.items()} for it in s.items],
            "doc_type": _enc(asdict(s.doc_type)), "pages": [asdict(p) for p in s.pages],
            "name": s.name, "sha256": s.sha256, "kind": s.kind, "tier": s.tier, "method": s.method,
            "transform": _enc(s.transform), "copy_mark": s.copy_mark, "vat_mode": s.vat_mode, "has_xml": s.has_xml,
            "agreement": _enc(s.agreement)}


def state_load(d: dict) -> State:
    return State(fields={k: _field_load(v) for k, v in d["fields"].items()},
                 items=[{k: (_field_load(v) if isinstance(v, dict) else v) for k, v in it.items()} for it in d["items"]],
                 doc_type=_field_load(d["doc_type"]), pages=[Page(**p) for p in d["pages"]], name=d["name"],
                 sha256=d["sha256"], kind=d["kind"], tier=d["tier"], method=d["method"], transform=_dec(d.get("transform")),
                 copy_mark=d["copy_mark"], vat_mode=d["vat_mode"], agreement=_dec(d.get("agreement") or {}),
                 has_xml=d.get("has_xml", False))


def rejudge(out: dict, **kw) -> dict:
    """Judge a stored extraction again (``out`` as ``extract`` returned it); keyword arguments as ``judge``."""
    return judge(state_load(out["state"]), **kw)


def _present(fields, name) -> bool:
    f = fields.get(name)
    return f is not None and f.present


def _same_value(name: str, a, b) -> bool:
    """Two readings of one field agree: names by their skeleton key (spacing and lost marks are not a
    disagreement), identifiers without spaces, everything else exactly."""
    kind = FIELD_KINDS.get(name)
    if kind == "party":
        ka = (a or {}).get("key") or T.core_key(str((a or {}).get("core") or ""))
        kb = (b or {}).get("key") or T.core_key(str((b or {}).get("core") or ""))
        return bool(ka) and ka == kb
    if kind in ("code", "text"):
        return "".join(str(a).split()).lower() == "".join(str(b).split()).lower()
    return str(a) == str(b)


def _decided(f) -> bool:
    """A value the person typed or confirmed."""
    return f is not None and ("typed_by_person" in f.checks or "confirmed_by_person" in f.checks)


# checks that are arithmetic between read numbers: on an OCR'd page a failure is most often a misread
ARITHMETIC = ("line_arithmetic", "subtotal_sum", "discount_arithmetic", "vat_7_percent", "grand_total", "wht",
              "amount_in_words")


def _normalise(name: str, v):
    kind = FIELD_KINDS.get(name)
    s = str(v)
    if kind == "money":
        return T.parse_money(s)
    if kind == "tax_id":
        d = "".join(ch for ch in T.arabic(s) if ch.isdigit())
        return d if len(d) == 13 else None
    if kind == "date":
        hit = T.parse_date(s)
        return hit[0] if hit else None
    if kind == "party":
        return T.legal_form(s) or {"legal_form": "unknown", "core": s.strip(), "key": T.core_key(s.strip())}
    return T.arabic(s).strip() or None


def _questions(fields, items, checks, pages, dtype, triage, *, absent: set = frozenset(),
               accepted: set = frozenset(), ocr: bool = False) -> list[Question]:
    """``ocr``: the document was read from a page image (scan or photo). There a failed arithmetic check
    first asks the person to confirm each number it involves that nobody has confirmed yet — the numbers
    are more likely misread than misprinted; only once they are all confirmed and it still fails is it a
    document defect (``check_failed``). Line-level failures on such a page do not block: the lines are
    recorded as unverified (the header numbers are what the register keeps)."""
    qs: list[Question] = []
    have = set()

    def add(q: Question):
        if q.key not in have:
            have.add(q.key)
            qs.append(q)

    scope = set(TRIAGE_FIELDS) if triage else set(FIELD_KINDS)
    failed_fields = set()
    # the triage rule (AM2): a photo's five fields are either read with confidence or asked about
    required = set(REQUIRED.get(dtype, ("seller_name", "document_date", "total")))
    if triage:
        required |= set(TRIAGE_FIELDS)
    for n in sorted(required & scope):
        f = fields.get(n)
        if n in absent:
            failed_fields.add(n)      # the person confirmed the document does not print it: not asked again
            continue
        if (f is None or not f.present) and not (n == "vat_amount" and dtype in ("receipt", "invoice")):
            model = [c for c in (f.candidates if f is not None else []) if str(c.get("method", "")).startswith(("vision", "model"))]
            if model:
                # a model read what the text could not: its reading is the proposal, the person decides
                add(Question("low_confidence", subject=n, fieldname=n, reason="only a model read this value",
                             proposed=model[0].get("normalized"), evidence={"candidates": model}))
            else:
                add(Question("field_missing", subject=n, fieldname=n, reason="could not be read" if triage else f"a {dtype} must show this"))
            failed_fields.add(n)
    for c in checks:
        if c.status != "fail" or c.code in accepted:
            continue
        if c.code == "document_type":
            add(Question("document_type_unknown", reason="the document's title does not say what it is"))
        elif c.code == "required_fields":
            continue          # asked above, field by field
        elif c.code == "duplicate":
            add(Question("duplicate_candidate", reason=c.message, evidence={"match": c.actual}))
        elif c.code == "original_copy":
            add(Question("copy_not_original", reason="this is marked สำเนา (copy): input VAT needs the original",
                         blocking=False))
        elif ocr and c.code in ARITHMETIC:
            money = [n for n in c.fields if n in MONEY_FIELDS and n in scope and n not in failed_fields and n not in absent]
            unread = [n for n in money if fields.get(n) is None or not fields[n].present]
            # a read value nobody confirmed (a sum computed from misread lines is not a reading: not offered)
            unsure = [n for n in money if n not in unread and not _decided(fields[n]) and fields[n].source is not None]
            if unread or unsure:
                for n in unsure:
                    add(Question("low_confidence", subject=n, fieldname=n, proposed=fields[n].normalized,
                                 reason=f"the numbers do not add up ({c.code}): please confirm what the page shows",
                                 evidence={"check": c.code, "expected": c.expected, "actual": c.actual,
                                           "raw_text": fields[n].source.raw_text}))
                for n in unread:
                    model = [x for x in (fields[n].candidates if fields.get(n) is not None else [])
                             if str(x.get("method", "")).startswith(("vision", "model"))]
                    if model:
                        add(Question("low_confidence", subject=n, fieldname=n, reason="only a model read this value",
                                     proposed=model[0].get("normalized"), evidence={"candidates": model, "check": c.code}))
                    else:
                        add(Question("field_missing", subject=n, fieldname=n,
                                     reason=f"could not be read, and the numbers do not add up without it ({c.code})",
                                     evidence={"check": c.code}))
                failed_fields.update(unread + unsure)
            elif triage or c.code in ("line_arithmetic", "subtotal_sum"):
                continue      # the lines (recorded as unverified), or a photo's fields outside its five
            else:
                add(Question("check_failed", subject=c.code, reason=c.message or f"{c.code} failed",
                             evidence={"expected": c.expected, "actual": c.actual}))
                failed_fields.update(c.fields)
        elif c.code.startswith("source_agreement:"):
            n = c.code.split(":", 1)[1]
            add(Question("source_conflict", subject=n, fieldname=n, reason=c.message, proposed=c.expected,
                         evidence={"other": c.actual}))
            failed_fields.add(n)
        elif triage and (not (set(c.fields) & scope) or "line_items" in c.fields):
            continue          # a photo is triage: no line items, no checks outside its five fields
        elif set(c.fields) & scope and set(c.fields) & scope <= failed_fields:
            continue          # already asked about every field this check covers
        else:
            add(Question("check_failed", subject=c.code, reason=c.message or f"{c.code} failed",
                         evidence={"expected": c.expected, "actual": c.actual}))
            failed_fields.update(c.fields)
    # confidence thresholds (the card: money / VAT / WHT / tax ID / bank account 0.95, the rest 0.85)
    low_pages = {p.number for p in pages if p.method == "ocr" and p.ocr_conf is not None and p.ocr_conf < PAGE_OCR_THRESHOLD}
    for n, f in fields.items():
        if n not in scope or n in failed_fields or not f.present:
            continue
        kind = FIELD_KINDS.get(n)
        need = STRICT_THRESHOLD if kind in STRICT_KINDS else ORDINARY_THRESHOLD
        if n in ("vat_rate", "wht_rate", "currency", "amount_in_words", "seller_address", "buyer_address", "due_date"):
            need = min(need, 0.6)
        if f.confidence < need:
            add(Question("low_confidence", subject=n, fieldname=n,
                         reason=f"read with confidence {f.confidence:.2f} (needs {need:.2f})", proposed=f.normalized,
                         evidence={"raw_text": f.source.raw_text if f.source else None,
                                   "candidates": [c.get("normalized") for c in f.candidates]}))
        elif f.source is not None and f.source.page in low_pages and kind in STRICT_KINDS and f.confidence < CORROBORATED:
            add(Question("page_quality", subject=f"p{f.source.page}", reason=f"page {f.source.page} OCR confidence below {PAGE_OCR_THRESHOLD:.0f}"))
    # a labelled value the engine could not read (the label is there, the value is not legible)
    for n, f in fields.items():
        if n in scope and not f.present and "unreadable" in f.checks and n not in failed_fields:
            add(Question("low_confidence", subject=n, fieldname=n, reason="the label is printed but its value could not be read",
                         evidence={"raw_text": f.value}))
    # a model's value for a field no deterministic source read: a question with the proposal, never a number
    for n, f in fields.items():
        if n in scope and not f.present and f.candidates and n not in failed_fields:
            add(Question("low_confidence", subject=n, fieldname=n, reason="only a model read this value",
                         proposed=f.candidates[0].get("normalized"), evidence={"candidates": f.candidates}))
    if not triage:
        for it in items:
            for k in ("amount",):
                f = it.get(k)
                if f is not None and f.present and f.confidence < STRICT_THRESHOLD:
                    add(Question("low_confidence", subject=f"line{it['row']}.{k}", fieldname=f"line_items[{it['row']}].{k}",
                                 reason=f"line {it['row']} amount read with confidence {f.confidence:.2f}",
                                 proposed=f.normalized))
    return qs
