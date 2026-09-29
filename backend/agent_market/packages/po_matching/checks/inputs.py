"""What a round's uploads are: purchase orders, goods receipts, and what the supplier bills.

The parse step reads each upload once and stores what it read (parsed.json, format
``am.pomatch.v1``); preflight and compute judge the stored reading. The runner tells the parse
step nothing about a file's role, so the role comes from the file itself:

  * a spreadsheet (Excel, CSV) is read sheet by sheet. A sheet is a table of purchase order
    lines (``po``), of goods receipt lines (``gr``) or of invoice lines (``invoice``) — decided
    by its own column headers and the title above them. One workbook may hold all three. A sheet
    whose headers fit two kinds, or none with certainty, is not guessed: preflight asks.
  * a PDF, an e-Tax XML or a picture is a document the supplier sent — a tax invoice, an
    invoice, a credit note, a debit note — read by the E2 engine exactly as the invoice agent
    reads it. The purchase order it names and, on a credit or debit note, the invoice it
    corrects are looked for in its text layer or its XML; a scan says neither, and then a
    person is asked rather than a link guessed.

An export often prints the PO number, the supplier and the date on the first line of each
document only. Those values are carried down to the lines under them, and each line records
which of its values were carried and from which row.

Every row keeps the cells it was read from. Nothing is computed here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ....engine import cells as C
from ....engine.e2 import thai_text as T
from ....engine.tabular import col_letter, read_workbook, sha256_file
from . import units as U

FORMAT = "am.pomatch.v1"
SHEET_EXT = (".xlsx", ".xlsm", ".csv", ".txt")
DOC_EXT = (".pdf", ".xml", ".jpg", ".jpeg", ".png")
HEADER_SCAN = 40
KINDS = ("po", "gr", "invoice")
KIND_TH = {"po": "ใบสั่งซื้อ (PO)", "gr": "ใบรับของ (GR)", "invoice": "ใบกำกับภาษีหรือใบแจ้งหนี้", "unused": "ไม่ใช้"}

# ── headers ──────────────────────────────────────────────────────────────
# A header cell (C.h: no spaces or punctuation, lower case) → the field it holds. The words that
# belong to one kind of table only come first: they decide what the table is.

PO_NUMBER = ("เลขที่ใบสั่งซื้อ", "ใบสั่งซื้อเลขที่", "เลขที่po", "เลขpo", "poเลขที่", "pono", "ponumber", "po", "purchaseorder",
             "purchaseorderno", "purchaseordernumber", "ใบสั่งซื้อ", "เลขที่สั่งซื้อ", "อ้างอิงpo", "อ้างอิงใบสั่งซื้อ", "poref", "porefno",
             "poreference", "refpo", "orderno", "ordernumber")
GR_NUMBER = ("เลขที่ใบรับของ", "เลขที่ใบรับสินค้า", "ใบรับของเลขที่", "ใบรับสินค้าเลขที่", "เลขที่รับของ", "เลขที่รับสินค้า", "เลขที่gr",
             "grno", "grnumber", "gr", "grn", "grnno", "goodsreceipt", "goodsreceiptno", "goodsreceivedno", "receiptno",
             "receivingno", "เลขที่ใบตรวจรับ", "ใบตรวจรับเลขที่")
GR_DATE = ("วันที่รับของ", "วันที่รับสินค้า", "วันที่รับ", "วันที่ตรวจรับ", "grdate", "receiptdate", "receiveddate", "receivingdate",
           "dateofreceipt")
GR_QTY = ("จำนวนรับ", "จำนวนที่รับ", "จำนวนรับจริง", "ปริมาณรับ", "ปริมาณที่รับ", "รับจริง", "จำนวนที่รับจริง", "receivedqty", "qtyreceived",
          "receivedquantity", "quantityreceived", "grqty", "rcvqty")
INV_NUMBER = ("เลขที่ใบกำกับภาษี", "เลขที่ใบกำกับ", "ใบกำกับภาษีเลขที่", "เลขที่ใบแจ้งหนี้", "ใบแจ้งหนี้เลขที่", "เลขที่ใบกำกับภาษี/ใบแจ้งหนี้",
              "เลขที่ใบส่งของ/ใบกำกับภาษี", "invoiceno", "invoicenumber", "invno", "invnumber", "taxinvoiceno", "taxinvoicenumber",
              "invoice", "taxinvoice", "billno", "เลขที่บิล", "เลขที่ใบลดหนี้", "เลขที่ใบเพิ่มหนี้")
INV_DATE = ("วันที่ใบกำกับภาษี", "วันที่ใบกำกับ", "วันที่ใบแจ้งหนี้", "invoicedate", "invdate", "taxinvoicedate", "billdate")
PO_DATE = ("วันที่ใบสั่งซื้อ", "วันที่สั่งซื้อ", "วันที่po", "podate", "orderdate", "purchasedate")
ANY_DATE = ("วันที่", "วันที่เอกสาร", "วันเดือนปี", "date", "docdate", "documentdate", "postingdate")
ANY_NUMBER = ("เลขที่เอกสาร", "เลขที่", "เอกสารเลขที่", "docno", "documentno", "documentnumber", "number", "no")

COMMON = {
    "supplier_name": ("ผู้ขาย", "ชื่อผู้ขาย", "ผู้จำหน่าย", "ชื่อผู้จำหน่าย", "เจ้าหนี้", "ชื่อเจ้าหนี้", "ผู้จัดจำหน่าย", "คู่ค้า", "supplier",
                      "suppliername", "vendor", "vendorname", "seller", "sellername", "payee"),
    "supplier_tax_id": ("เลขประจำตัวผู้เสียภาษี", "เลขประจำตัวผู้เสียภาษีผู้ขาย", "เลขผู้เสียภาษี", "เลขผู้เสียภาษีผู้ขาย",
                        "เลขที่ผู้เสียภาษี", "เลขประจำตัวผู้เสียภาษีอากร", "taxid", "taxno", "vendortaxid", "suppliertaxid", "sellertaxid",
                        "tin", "vatregno", "vatregistrationno"),
    "supplier_branch": ("สาขา", "สาขาผู้ขาย", "สาขาที่", "branch", "branchno", "sellerbranch"),
    "supplier_code": ("รหัสผู้ขาย", "รหัสเจ้าหนี้", "รหัสผู้จำหน่าย", "vendorcode", "suppliercode", "vendorno", "supplierno", "vendorid"),
    "line_no": ("ลำดับ", "ลำดับที่", "บรรทัด", "บรรทัดที่", "รายการที่", "ลำดับรายการ", "line", "lineno", "linenumber", "itemno", "seq",
                "lineitem", "no"),
    "item_code": ("รหัสสินค้า", "รหัสสินค้า/บริการ", "รหัส", "รหัสวัสดุ", "รหัสรายการ", "itemcode", "productcode", "materialcode",
                  "material", "materialno", "sku", "partno", "partnumber", "item", "productid"),
    "description": ("รายการ", "รายละเอียด", "รายการสินค้า", "ชื่อสินค้า", "ชื่อสินค้า/บริการ", "สินค้า/บริการ", "สินค้า", "รายละเอียดสินค้า",
                    "คำอธิบาย", "description", "descriptions", "itemdescription", "itemname", "productname", "details", "detail",
                    "particulars"),
    "quantity": ("จำนวน", "ปริมาณ", "จำนวนหน่วย", "qty", "quantity", "units"),
    "unit": ("หน่วย", "หน่วยนับ", "หน่วยวัด", "uom", "unit", "unitofmeasure", "um"),
    "unit_price": ("ราคาต่อหน่วย", "ราคา/หน่วย", "ราคาหน่วยละ", "หน่วยละ", "ราคา", "ราคาต่อหน่วย(ไม่รวมvat)", "unitprice", "price",
                   "rate", "unitcost", "priceperunit"),
    "amount": ("จำนวนเงิน", "มูลค่า", "ยอดเงิน", "รวมเงิน", "จำนวนเงินรวม", "มูลค่าสินค้า", "มูลค่าก่อนภาษี", "ยอดก่อนภาษี", "amount",
               "lineamount", "linetotal", "netamount", "extendedamount", "value", "extprice"),
    "currency": ("สกุลเงิน", "currency", "cur", "curr", "ccy"),
    "status": ("สถานะ", "สถานะเอกสาร", "สถานะใบสั่งซื้อ", "สถานะpo", "status", "postatus", "docstatus"),
    "note": ("หมายเหตุ", "note", "notes", "remark", "remarks", "memo"),
}
PO_ONLY = {
    "po_line": (),
    "ordered_qty": ("จำนวนสั่ง", "จำนวนที่สั่ง", "จำนวนสั่งซื้อ", "ปริมาณสั่งซื้อ", "orderqty", "orderedqty", "qtyordered", "poqty",
                    "orderquantity"),
    "vat": ("ภาษี", "ภาษีมูลค่าเพิ่ม", "vat", "vat%", "อัตราภาษี", "อัตราvat", "vatrate", "taxrate", "รหัสภาษี", "ประเภทภาษี", "vatcode",
            "taxcode", "taxtype"),
    "delivery_date": ("กำหนดส่ง", "กำหนดส่งของ", "วันที่กำหนดส่ง", "วันกำหนดส่ง", "วันที่ส่งมอบ", "กำหนดส่งมอบ", "deliverydate", "duedate",
                      "requireddate", "eta"),
    "terms": ("เงื่อนไขการส่งมอบ", "เงื่อนไขส่งมอบ", "เงื่อนไข", "เงื่อนไขการชำระ", "deliveryterms", "incoterms", "terms", "paymentterms"),
    "type": ("ประเภท", "ประเภทใบสั่งซื้อ", "ประเภทpo", "ประเภทรายการ", "ชนิด", "type", "potype", "ordertype", "linetype", "itemtype"),
    "valid_from": ("วันที่เริ่ม", "เริ่มใช้", "วันที่เริ่มสัญญา", "มีผลตั้งแต่", "validfrom", "startdate", "effectivefrom"),
    "valid_to": ("วันที่สิ้นสุด", "สิ้นสุด", "วันที่สิ้นสุดสัญญา", "มีผลถึง", "validto", "enddate", "expirydate", "effectiveto"),
    "limit": ("วงเงิน", "วงเงินรวม", "วงเงินสัญญา", "มูลค่าสัญญา", "limit", "blanketamount", "contractvalue", "agreementvalue",
              "maxamount"),
    "closed_date": ("วันที่ปิด", "วันที่ปิดpo", "วันที่ปิดใบสั่งซื้อ", "closeddate", "closedate"),
}
GR_ONLY = {
    "po_line": ("บรรทัดpo", "บรรทัดที่po", "ลำดับpo", "ลำดับในpo", "รายการที่ในpo", "poline", "polineno", "poitem", "polinenumber"),
    "delivery_note": ("เลขที่ใบส่งของ", "ใบส่งของ", "เลขที่ใบส่งสินค้า", "ใบส่งสินค้า", "deliverynote", "deliverynoteno", "dono", "dnno",
                      "deliveryorder"),
    "received_by": ("ผู้รับของ", "ผู้รับสินค้า", "ผู้ตรวจรับ", "receivedby", "receiver"),
}
INV_ONLY = {
    "po_line": GR_ONLY["po_line"],
    "vat_amount": ("ภาษีมูลค่าเพิ่ม", "ภาษี", "จำนวนภาษี", "ยอดภาษี", "ภาษีซื้อ", "vat", "vatamount", "taxamount", "tax", "vat7%"),
    "subtotal": ("ยอดก่อนภาษีรวม", "มูลค่ารวมก่อนภาษี", "รวมก่อนภาษี", "subtotal", "totalbeforevat", "totalexclvat", "netbeforevat"),
    "total": ("ยอดรวม", "รวมทั้งสิ้น", "ยอดรวมทั้งสิ้น", "จำนวนเงินรวมทั้งสิ้น", "ยอดสุทธิ", "ยอดรวมภาษี", "total", "grandtotal",
              "totalamount", "invoicetotal", "totalinclvat"),
    "fx_rate": ("อัตราแลกเปลี่ยน", "อัตราแลกเปลี่ยน(บาท)", "exchangerate", "fxrate", "exrate", "rateofexchange"),
    "doc_type": ("ประเภทเอกสาร", "ชนิดเอกสาร", "ประเภท", "doctype", "documenttype", "type"),
    "ref_invoice": ("อ้างอิงใบกำกับภาษี", "อ้างถึงใบกำกับภาษี", "ใบกำกับภาษีเดิม", "เลขที่ใบกำกับภาษีเดิม", "อ้างอิงเอกสารเดิม",
                    "อ้างถึง", "refinvoice", "refinvoiceno", "originalinvoice", "originalinvoiceno", "originaldocno", "reference"),
    "due_date": ("วันครบกำหนด", "ครบกำหนด", "วันที่ครบกำหนด", "กำหนดชำระ", "duedate", "paymentdue"),
    "buyer_tax_id": ("เลขประจำตัวผู้เสียภาษีผู้ซื้อ", "เลขผู้เสียภาษีผู้ซื้อ", "buyertaxid"),
}
TITLE_WORDS = {
    "gr": ("ใบรับของ", "ใบรับสินค้า", "ใบตรวจรับ", "รายงานการรับสินค้า", "รายงานการรับของ", "รายงานรับสินค้า", "goodsreceipt",
           "goodsreceived", "receivingreport", "grn"),
    "invoice": ("ทะเบียนใบกำกับภาษี", "รายงานภาษีซื้อ", "ทะเบียนใบแจ้งหนี้", "รายการใบแจ้งหนี้", "รายการใบกำกับภาษี", "ใบกำกับภาษี",
                "ใบแจ้งหนี้", "invoiceregister", "invoicelist", "purchasevat", "apinvoice", "invoices"),
    "po": ("ใบสั่งซื้อ", "รายงานการสั่งซื้อ", "รายงานใบสั่งซื้อ", "ทะเบียนใบสั่งซื้อ", "purchaseorder", "purchaseorders", "poreport", "polist",
           "openpo"),
}
SHEET_NAMES = {
    "po": ("po", "pos", "purchaseorder", "purchaseorders", "ใบสั่งซื้อ"),
    "gr": ("gr", "grn", "goodsreceipt", "receiving", "ใบรับของ", "ใบรับสินค้า"),
    "invoice": ("inv", "invoice", "invoices", "taxinvoice", "ใบแจ้งหนี้", "ใบกำกับภาษี"),
}
TOTAL_WORDS = ("รวม", "ยอดรวม", "รวมทั้งสิ้น", "รวมเงิน", "รวมทั้งหมด", "total", "grandtotal", "subtotal", "sum")
# the values of a document's first line that the lines under it share
CARRIED = {
    "po": ("po_number", "po_date", "supplier_name", "supplier_tax_id", "supplier_code", "currency", "status", "type", "terms",
           "closed_date", "valid_from", "valid_to", "limit"),
    "gr": ("gr_number", "gr_date", "po_number", "supplier_name", "delivery_note", "status"),
    "invoice": ("invoice_number", "invoice_date", "supplier_name", "supplier_tax_id", "supplier_branch", "po_number", "currency",
                "fx_rate", "doc_type", "ref_invoice", "total", "subtotal", "vat_amount", "due_date", "buyer_tax_id"),
}
MONEY_FIELDS = ("amount", "vat_amount", "subtotal", "total", "limit")
NUMBER_FIELDS = ("quantity", "unit_price", "fx_rate")
DATE_FIELDS = ("po_date", "gr_date", "invoice_date", "delivery_date", "valid_from", "valid_to", "closed_date", "due_date")
DOC_TYPE_WORDS = (
    ("credit_note", ("ใบลดหนี้", "creditnote", "cn", "credit")),
    ("debit_note", ("ใบเพิ่มหนี้", "debitnote", "dn", "debit")),
    ("abbreviated_tax_invoice", ("ใบกำกับภาษีอย่างย่อ", "อย่างย่อ", "abbreviatedtaxinvoice", "abb")),
    ("full_tax_invoice", ("ใบกำกับภาษี", "ใบกำกับภาษีเต็มรูป", "ใบกำกับภาษี/ใบส่งของ", "ใบส่งของ/ใบกำกับภาษี", "taxinvoice", "เต็มรูป")),
    ("receipt", ("ใบเสร็จรับเงิน", "ใบเสร็จ", "receipt")),
    ("invoice", ("ใบแจ้งหนี้", "ใบวางบิล", "ใบเรียกเก็บเงิน", "invoice", "billingnote")),
)
# A reference to a purchase order, or to the invoice a note corrects, in a document's text. The
# label is looked for on the text's skeleton (a PDF's text layer loses Thai marks: "อ้างอิงใบสังซือ
# เลขที"), the reference is read from the text as written, right after the label. "PO" is a label
# only with something between it and the number ("PO No. 123", "PO: 123"): in "PO6909-001" it is
# part of the number.
PO_LABEL = T.S(r"(?:เลขที่ใบสั่งซื้อ|ใบสั่งซื้อเลขที่|อ้างอิงใบสั่งซื้อ(?:เลขที่)?|อ้างถึงใบสั่งซื้อ(?:เลขที่)?|ตามใบสั่งซื้อ(?:เลขที่)?|"
               r"เลขที่PO|อ้างอิงPO|PurchaseOrder(?:No\.?|Number)?|(?<![A-Za-z0-9])P\.?O\.?(?:No\.?|Number|Ref\.?|Reference)|"
               r"(?<![A-Za-z0-9])P\.?O\.?(?=[:：#])|YourOrder(?:No\.?)?|OrderRef\.?)", re.I)
INV_LABEL = T.S(r"(?:อ้างถึงใบกำกับภาษี(?:เลขที่)?|อ้างอิงใบกำกับภาษี(?:เลขที่)?|ใบกำกับภาษีเดิม(?:เลขที่)?|อ้างถึงใบแจ้งหนี้(?:เลขที่)?|"
                r"อ้างอิงใบแจ้งหนี้(?:เลขที่)?|Ref(?:erence)?\.?(?:Tax)?Invoice(?:No\.?|Number)?|Original(?:Tax)?Invoice(?:No\.?|Number)?|"
                r"AgainstInvoice(?:No\.?|Number)?)", re.I)
REF_VALUE = re.compile(r"[\s.:：#]*([A-Z0-9][A-Z0-9\-/_]{2,24})", re.I)


@dataclass
class Upload:
    """One upload as the parse step read it."""
    kind: str                 # tables | document | other
    key: str
    name: str
    sha256: str
    data: dict

    def summary(self) -> dict:
        s = {"key": self.key, "doc_key": self.key, "name": self.name, "kind": self.kind}
        if self.kind == "tables":
            s["tables"] = [{"kind": t["kind"], "sheet": t["sheet"], "header_row": t["header_row"], "rows": len(t["rows"]),
                            "columns": t["columns"]} for t in self.data["tables"]]
        elif self.kind == "document":
            x = self.data["extraction"]
            s.update({"tier": x["document"]["tier"], "method": x["method"], "pages": x["document"]["pages"],
                      "document_type": x["document_type"]["normalized"], "questions": len(x["questions"]),
                      "triage_only": x["document"]["triage_only"]})
        else:
            s["reason"] = self.data.get("reason")
        return s


def dump(u: Upload) -> dict:
    return {"format": FORMAT, "kind": u.kind, "key": u.key, "name": u.name, "sha256": u.sha256, "data": u.data}


def load(d: dict) -> Upload:
    if d.get("format") != FORMAT:
        raise ValueError(f"unknown parsed format {d.get('format')!r}")
    return Upload(kind=d["kind"], key=d["key"], name=d["name"], sha256=d["sha256"], data=d["data"])


def parse(path: str, name: str, mapping: dict | None = None) -> Upload:
    ext = os.path.splitext(name)[1].lower()
    if ext in DOC_EXT:
        return _document(path, name)
    sha = sha256_file(path)
    key = "d" + sha[:10]
    if ext not in SHEET_EXT:
        return Upload("other", key, name, sha, {"reason": "unsupported_type", "detail": ext,
                                                "say": "อ่านได้เฉพาะไฟล์ Excel หรือ CSV ของใบสั่งซื้อและใบรับของ และ PDF หรือ e-Tax ของใบแจ้งหนี้"})
    wb = read_workbook(path, name)
    if wb.error:
        return Upload("other", key, name, sha, {"reason": "unreadable", "detail": wb.error, "say": "เปิดไฟล์นี้ไม่ได้"})
    own = (mapping or {}).get("po_matching") or {}
    tables = []
    for i, sheet in enumerate(wb.sheets):
        t = _table(sheet, i, own)
        if t is not None:
            tables.append(t)
    if not tables:
        return Upload("other", key, name, sha, {
            "reason": "unrecognised_sheet",
            "say": "ไม่พบหัวคอลัมน์ของใบสั่งซื้อ ใบรับของ หรือใบแจ้งหนี้ (เลขที่เอกสาร รายการ และจำนวนหรือจำนวนเงิน)"})
    return Upload("tables", key, name, sha, {"tables": tables})


# ── a document the supplier sent ─────────────────────────────────────────

def _document(path: str, name: str) -> Upload:
    from ....engine.e2.extract import extract

    out = extract(path, name)
    sha = out["document"]["sha256"]
    return Upload("document", "d" + sha[:10], name, sha, {"extraction": out, "refs": references(path, name)})


def references(path: str, name: str) -> dict:
    """The purchase orders a document names and the invoice a note corrects — from the e-Tax XML
    or the PDF's text layer. A scan or a picture has neither: both lists are empty and ``read``
    says so, and the link is then asked of a person."""
    ext = os.path.splitext(name)[1].lower()
    texts, how = [], "none"
    try:
        if ext == ".xml":
            with open(path, "rb") as f:
                texts, how = [f.read().decode("utf-8", "replace")], "xml"
        elif ext == ".pdf":
            from ....engine.e2 import etax
            from ....engine.e2.words import build_lines, pdf_text_words

            emb = etax.embedded_xml(path)
            if emb and etax.is_etda(emb[1]):
                texts.append(emb[1].decode("utf-8", "replace"))
                how = "xml"
            words, _pages = pdf_text_words(path)
            lines = [T.fix_text_layer(l.text) for l in build_lines(words)] if words else []
            if lines:
                texts += lines
                how = "xml+text" if how == "xml" else "text"
    except Exception:  # noqa: BLE001 — a document whose references cannot be read has none; a person is asked
        texts, how = [], "none"
    po, inv = [], []
    for t in texts:
        if how.startswith("xml") and t.lstrip().startswith("<"):
            po += _xml_refs(t, ("BuyerOrderReferencedDocument",))
            inv += _xml_refs(t, ("AdditionalReferencedDocument", "InvoiceReferencedDocument"))
            continue
        s = T.arabic(t)
        po += _text_refs(PO_LABEL, s)
        inv += _text_refs(INV_LABEL, s)
    return {"read": how, "po_numbers": _unique(po), "ref_invoices": _unique(inv)}


def _text_refs(label, text: str) -> list:
    out = []
    for _start, end, _m in T.sfinditer(label, text):
        m = REF_VALUE.match(text, end)
        if m and _is_ref(m.group(1)):
            out.append(m.group(1).strip(".-/"))
    return out


def _xml_refs(xml: str, tags: tuple) -> list:
    out = []
    for tag in tags:
        for block in re.findall(rf"<(?:\w+:)?{tag}\b[^>]*>(.*?)</(?:\w+:)?{tag}>", xml, re.S):
            m = re.search(r"<(?:\w+:)?IssuerAssignedID\b[^>]*>([^<]+)<", block)
            if m and _is_ref(m.group(1).strip()):
                out.append(m.group(1).strip())
    return out


def _is_ref(s: str) -> bool:
    """A reference has a digit in it: "PO No" followed by the word "Date" is not one."""
    return any(ch.isdigit() for ch in s) and len(s.strip(".-/")) >= 3


def _unique(xs: list) -> list:
    seen, out = set(), []
    for x in xs:
        k = re.sub(r"[^0-9A-Za-z]", "", x).upper()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ── a table ──────────────────────────────────────────────────────────────

def _fields_for(kind: str) -> dict:
    """{field: header words} for one kind of table, the kind's own words first."""
    own = {"po": PO_ONLY, "gr": GR_ONLY, "invoice": INV_ONLY}[kind]
    number = {"po": ("po_number", PO_NUMBER), "gr": ("gr_number", GR_NUMBER), "invoice": ("invoice_number", INV_NUMBER)}[kind]
    date = {"po": ("po_date", PO_DATE), "gr": ("gr_date", GR_DATE), "invoice": ("invoice_date", INV_DATE)}[kind]
    out = {number[0]: number[1], date[0]: date[1]}
    if kind != "po":
        out["po_number"] = PO_NUMBER
    if kind == "gr":
        out["quantity"] = GR_QTY + COMMON["quantity"]
    if kind == "po":
        out["quantity"] = own["ordered_qty"] + COMMON["quantity"]
    for f, words in own.items():
        if f not in ("ordered_qty",) and words:
            out[f] = words
    for f, words in COMMON.items():
        out.setdefault(f, words)
    # the document's own number and date, under a header every kind of table uses
    out[number[0]] = out[number[0]] + ANY_NUMBER
    out[date[0]] = out[date[0]] + ANY_DATE
    return out


def _columns(heads: list, kind: str, mapping: dict) -> dict:
    fields = _fields_for(kind)
    cols: dict[str, int] = {}
    for f, header in (mapping.get(kind) or {}).items():
        if f in fields and C.h(header) in heads:
            cols[f] = heads.index(C.h(header))
    # a header is matched to the field that names it most specifically: every field's own words
    # are tried in the order they are listed, the fields in the order of _fields_for
    for f, words in fields.items():
        if f in cols:
            continue
        for w in words:
            hit = next((j for j, h in enumerate(heads) if h == w and j not in cols.values()), None)
            if hit is not None:
                cols[f] = hit
                break
    return cols


def _signs(heads: list, title: str, sheet_name: str = "") -> dict:
    """What the headers, the title and the sheet's name say the table is: {kind: [what said so]}."""
    out = {k: [] for k in KINDS}
    named = C.h(sheet_name)
    for kind, names in SHEET_NAMES.items():
        if named in names:
            out[kind].append(f"แผ่นงานชื่อ “{sheet_name}”")
    for h in heads:
        if h in GR_NUMBER or h in GR_DATE or h in GR_QTY:
            out["gr"].append(f"หัวคอลัมน์ “{h}”")
        if h in INV_NUMBER or h in INV_DATE:
            out["invoice"].append(f"หัวคอลัมน์ “{h}”")
        if h in PO_ONLY["ordered_qty"] or h in PO_DATE:
            out["po"].append(f"หัวคอลัมน์ “{h}”")
    low = C.h(title)
    for kind, words in TITLE_WORDS.items():
        hit = next((w for w in words if w in low), None)
        if hit:
            out[kind].append(f"หัวกระดาษมีคำว่า “{hit}”")
    return out


def _kind(heads: list, title: str, sheet_name: str = "") -> tuple[str | None, dict]:
    """po / gr / invoice from what the sheet itself says; None when it does not say."""
    signs = _signs(heads, title, sheet_name)
    has_po_number = any(h in PO_NUMBER for h in heads)
    # a number of its own beside the PO's: a document that refers to a PO — or a PO. Not decided here.
    own_number = any(h in ANY_NUMBER for h in heads)
    by_header = {k: [s for s in v if s.startswith("หัวคอลัมน์")] for k, v in signs.items()}
    if by_header["gr"] and by_header["invoice"]:
        return None, signs
    if by_header["gr"]:
        return "gr", signs
    if by_header["invoice"]:
        return "invoice", signs
    # the title decides between the kinds when no column does; the most specific title wins
    # ("ใบสั่งซื้อ" is also a column of a goods receipt report)
    titled = [k for k in ("gr", "invoice", "po") if signs[k]]
    if len(titled) == 1:
        return titled[0], signs
    if by_header["po"] or (has_po_number and not titled and not own_number):
        return "po", signs
    if titled:
        return (titled[0] if titled[0] != "po" or len(titled) == 1 else None), signs
    return None, signs


def _usable(kind: str, cols: dict) -> bool:
    what = "description" in cols or "item_code" in cols
    if kind == "po":
        return "po_number" in cols and what and ("quantity" in cols or "amount" in cols or "limit" in cols)
    if kind == "gr":
        return "quantity" in cols and ("po_number" in cols or "item_code" in cols) and ("gr_number" in cols or "gr_date" in cols)
    return "invoice_number" in cols and ("amount" in cols or "total" in cols)


def _above(sheet, header_at: int) -> list[str]:
    out = []
    for r in sheet.rows[:header_at]:
        t = " ".join(C.text(c) for c in r if C.text(c))
        if t:
            out.append(t[:200])
    return out


def _table(sheet, index: int, mapping: dict) -> dict | None:
    forced = (mapping.get("sheets") or {}).get(sheet.name)
    for i, row in enumerate(sheet.rows[:HEADER_SCAN]):
        heads = [C.h(c) for c in row]
        if sum(1 for h in heads if h) < 3:
            continue
        title = _above(sheet, i)
        kind, signs = _kind(heads, " ".join(title), sheet.name)
        if forced in KINDS:
            kind = forced
        tried = [kind] if kind else list(KINDS)
        fits = [(k, _columns(heads, k, mapping)) for k in tried]
        fits = [(k, c) for k, c in fits if _usable(k, c)]
        if not fits:
            continue
        if kind is None and len(fits) > 1:
            # the headers fit more than one kind and nothing decides: read as each of them, and ask.
            # The same column is another field in another kind of table ("เลขที่" is the invoice's
            # number in one and the receipt's in the other), so the reading is kept per kind.
            k, cols = fits[0]
            t = _read(sheet, index, i, k, cols, title, signs)
            t["kind"], t["could_be"] = None, [x for x, _ in fits]
            t["read_as"] = {x: _read(sheet, index, i, x, c, title, signs) for x, c in fits}
            return t
        k, cols = fits[0]
        return _read(sheet, index, i, k, cols, title, signs)
    return None


def doc_type_of(v) -> str:
    k = C.h(v)
    if not k:
        return ""
    for dtype, words in DOC_TYPE_WORDS:
        if any(k == w or (len(w) > 3 and w in k) for w in words):
            return dtype
    return ""


def _read(sheet, index: int, header_at: int, kind: str, cols: dict, title: list, signs: dict) -> dict:
    number = {"po": "po_number", "gr": "gr_number", "invoice": "invoice_number"}[kind]
    rows, skipped = [], []
    last: dict = {}
    for i in range(header_at + 1, len(sheet.rows)):
        r = sheet.rows[i]
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
            continue

        def cell(f):
            j = cols.get(f)
            return r[j] if j is not None and j < len(r) else None
        first = C.h(" ".join(C.text(c) for c in r[:3]))
        own_number = C.text(cell(number))
        has_line = any(C.text(cell(f)) for f in ("description", "item_code")) or C.number(cell("quantity")) is not None
        if any(first.startswith(w) for w in TOTAL_WORDS) and not own_number:
            skipped.append({"row": i + 1, "why": "total_row"})
            continue
        row = {"row": i + 1, "cells": {f: f"{col_letter(j)}{i + 1}" for f, j in cols.items()
                                        if j < len(r) and r[j] not in (None, "")}, "carried": {}}
        for f in cols:
            v = cell(f)
            if f in MONEY_FIELDS:
                row[f] = C.money(v)
            elif f in NUMBER_FIELDS:
                row[f] = C.number(v)
            elif f in DATE_FIELDS:
                row[f] = C.date_of(v)
                row[f + "_raw"] = C.text(v)
            elif f in ("supplier_tax_id", "buyer_tax_id"):
                row[f] = C.tax_id(v)
                row[f + "_raw"] = C.text(v)
            elif f == "doc_type":
                row[f] = doc_type_of(v)
                row[f + "_raw"] = C.text(v)
            elif f == "vat":
                row[f] = C.text(v)
            elif f == "currency":
                row[f] = C.text(v).upper()[:3]
            else:
                row[f] = C.text(v)
        if own_number:
            # a new document: what the lines under it share starts again
            if last.get(number) != own_number:
                last = {number: own_number, "_row": i + 1}
            for f in CARRIED[kind]:
                if row.get(f) not in (None, ""):
                    last[f] = row[f]
                    last["_row_" + f] = i + 1
        for f in CARRIED[kind]:
            if f in cols and row.get(f) in (None, "") and last.get(f) not in (None, ""):
                row[f] = last[f]
                row["carried"][f] = last.get("_row_" + f) or last.get("_row")
        if not row.get(number) and kind != "gr":
            skipped.append({"row": i + 1, "why": "no_document_number", "text": C.text(cell("description"))[:60]})
            continue
        if not has_line and kind != "invoice":
            skipped.append({"row": i + 1, "why": "no_line", "text": own_number})
            continue
        if kind == "invoice" and not has_line:
            row["header_only"] = True
        rows.append(row)
    return {"kind": kind, "sheet": sheet.name, "sheet_index": index, "header_row": header_at + 1,
            "columns": {f: col_letter(j) for f, j in cols.items()}, "title": title[:8], "signs": signs,
            "rows": rows, "skipped": skipped}


def service_type(v) -> bool:
    return U.key(v) in tuple(U.key(w) for w in U.SERVICE_TYPES)


def blanket_type(v) -> bool:
    k = U.key(v)
    return bool(k) and any(k == U.key(w) or U.key(w) in k for w in U.BLANKET_TYPES)
