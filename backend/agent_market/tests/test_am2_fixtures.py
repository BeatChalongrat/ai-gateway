"""AM2 Phase A — the fixture pack describes its documents (no engine involved).

For every fixture: the file is there and unchanged (index sha256); the golden
truth is arithmetically what the checks say (a designed failure really
fails, a clean one really passes); and the document itself carries the
truth — the digital PDFs' text layer holds the number, the tax ID, the date
and the total the golden JSON claims, the e-Tax XML carries the same totals,
the scans and photos have no text layer (OCR is the only way in). The
template and expense fixtures are checked the same way. This is the review
that gates Phase C: if a golden value were wrong, this fails before any
parser is scored against it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unittest
import zipfile
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures")
E2 = os.path.join(FIX, "e2")

TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _tax_ok(t: str) -> bool:
    return len(t) == 13 and t.isdigit() and (11 - sum(int(c) * (13 - i) for i, c in enumerate(t[:12])) % 11) % 10 == int(t[12])


def _pdf_text(path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.translate(TH))


class E2Pack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = _load(os.path.join(E2, "index.json"))["fixtures"]
        cls.exp = {f["id"]: _load(os.path.join(E2, "expected", f["id"] + ".json")) for f in cls.index}
        cls.org = _load(os.path.join(E2, "org.json"))

    def test_every_file_is_there_and_unchanged(self):
        self.assertEqual(len(self.index), 24)
        for f in self.index:
            for rel, sha in f["sha256"].items():
                with open(os.path.join(E2, rel), "rb") as fh:
                    self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), sha, rel)

    def test_every_class_the_card_asks_for_is_covered(self):
        classes = {e["class"] for e in self.exp.values()}
        self.assertTrue({"digital", "scan300", "scan200", "photo", "etax_pdf", "etax_xml"} <= classes)
        types = {e["truth"]["document_type"] for e in self.exp.values()}
        self.assertTrue({"full_tax_invoice", "abbreviated_tax_invoice", "receipt", "invoice"} <= types)
        clean = [i for i, e in self.exp.items() if "zero_questions" in e["rules"]]
        self.assertIn("d01_clean_full_tax_invoice", clean)
        failing = {q.split(":")[0] + ":" + q.split(":")[1] if ":" in q else q
                   for e in self.exp.values() for q in e["questions"]["must"]}
        for q in ("check_failed:line_arithmetic", "check_failed:vat_7_percent", "duplicate_candidate",
                  "check_failed:buyer_match", "field_missing:seller_tax_id"):
            self.assertIn(q, failing)
        forms = {e["truth"]["seller_name"]["normalized"]["legal_form"] for e in self.exp.values()}
        self.assertTrue({"company_limited", "limited_partnership", "public_company_limited"} <= forms)

    def test_truth_arithmetic_matches_the_checks(self):
        for fid, e in self.exp.items():
            t, c = e["truth"], e["checks"]
            with self.subTest(fid):
                if t["seller_tax_id"]:
                    self.assertEqual(_tax_ok(t["seller_tax_id"]["value"]), c["seller_tax_id_format"] == "pass")
                lines = t["line_items"]
                if lines:
                    arith = all(D(l["amount"]) == (D(l["quantity"]) * D(l["unit_price"])).quantize(D("0.01")) for l in lines)
                    self.assertEqual(arith, c["line_arithmetic"] == "pass")
                    self.assertEqual(sum(D(l["amount"]) for l in lines), D(t["sum_lines"]))
                    self.assertEqual(D(t["sum_lines"]) - D(t["discount"]), D(t["subtotal"]) if t["vat_mode"] != "inclusive"
                                     else D(t["total"]))
                if t["vat_mode"] == "exclusive":
                    self.assertEqual(D(t["subtotal"]) + D(t["vat_amount"]), D(t["total"]))
                    ok = abs(D(t["vat_amount"]) - (D(t["subtotal"]) * D("0.07")).quantize(D("0.01"))) <= D("0.01")
                    self.assertEqual(ok, c["vat_7_percent"] == "pass")
                elif t["vat_mode"] == "inclusive":
                    self.assertEqual(D(t["subtotal"]) + D(t["vat_amount"]), D(t["total"]))
                    self.assertLessEqual(abs(D(t["vat_amount"]) - D(t["total"]) * 7 / 107), D("0.01"))
                if t["wht_amount"]:
                    self.assertEqual((D(t["subtotal"]) * D(t["wht_rate"]) / 100).quantize(D("0.01")), D(t["wht_amount"]))
                    self.assertEqual(D(t["total"]) - D(t["wht_amount"]), D(t["net_payable"]))
                if t["buyer_tax_id"] and c["buyer_match"] != "skip":
                    self.assertEqual(t["buyer_tax_id"]["value"] == self.org["tax_id"], c["buyer_match"] == "pass")

    def test_digital_text_layer_carries_the_truth(self):
        for fid, e in self.exp.items():
            if e["class"] not in ("digital", "etax_pdf"):
                continue
            t = e["truth"]
            text = _pdf_text(os.path.join(E2, e["files"][0]))
            flat = _norm(text)
            with self.subTest(fid):
                self.assertIn(_norm(t["document_number"]), flat)
                total = D(t["net_payable"] or t["total"])
                self.assertIn(f"{total:,.2f}", flat.replace("USD", ""))
                if t["seller_tax_id"]:
                    digits = re.sub(r"\D", "", text.translate(TH))
                    self.assertIn(t["seller_tax_id"]["value"], digits)
                if t["buyer_tax_id"]:
                    self.assertIn(t["buyer_tax_id"]["value"], re.sub(r"\D", "", text.translate(TH)))
                for l in t["line_items"][:3]:
                    self.assertIn(f"{D(l['amount']):,.2f}", flat)
                if e["provenance"]["pages"] == 2:
                    import pdfplumber

                    with pdfplumber.open(os.path.join(E2, e["files"][0])) as pdf:
                        self.assertEqual(len(pdf.pages), 2)
                        p1 = _norm(pdf.pages[0].extract_text() or "")
                        p2 = _norm(pdf.pages[1].extract_text() or "")
                    self.assertNotIn(f"{total:,.2f}", p1)   # the totals are on page 2 only
                    self.assertIn(f"{total:,.2f}", p2)

    def test_etax_xml_carries_the_same_totals(self):
        import xml.etree.ElementTree as ET

        from pypdf import PdfReader

        ram = "{urn:etda:uncefact:data:standard:TaxInvoice_ReusableAggregateBusinessInformationEntity:2}"
        for fid in ("e01_etax_pdf_embedded_xml", "e02_etax_xml_receipt"):
            e = self.exp[fid]
            path = os.path.join(E2, e["files"][0])
            if path.endswith(".pdf"):
                att = PdfReader(path).attachments
                self.assertEqual(list(att), ["ETDA-TaxInvoice.xml"])
                data = att["ETDA-TaxInvoice.xml"][0]
            else:
                with open(path, "rb") as fh:
                    data = fh.read()
            root = ET.fromstring(data)
            with self.subTest(fid):
                self.assertEqual(root.find(f".//{ram}GrandTotalAmount").text, e["truth"]["total"])
                self.assertEqual(root.find(f".//{ram}TaxTotalAmount").text, e["truth"]["vat_amount"])
                self.assertEqual(root.find(f".//{ram}SellerTradeParty/{ram}SpecifiedTaxRegistration/{ram}ID").text[:13],
                                 e["truth"]["seller_tax_id"]["value"])
                self.assertEqual(root.find(f".//{ram}IssueDateTime").text[:10], e["truth"]["document_date"])

    def test_scans_and_photos_have_no_text_layer(self):
        from PIL import Image

        for fid, e in self.exp.items():
            path = os.path.join(E2, e["files"][0])
            with self.subTest(fid):
                if e["class"] in ("scan200", "scan300"):
                    self.assertEqual(_norm(_pdf_text(path)), "")
                    import pypdfium2 as pdfium

                    doc = pdfium.PdfDocument(path)
                    w_pt = doc[0].get_width()
                    img = doc[0].get_objects()
                    doc.close()
                    self.assertAlmostEqual(w_pt, 595, delta=3)   # A4 width in points
                    self.assertEqual(e["truth"], self.exp[e["source_fixture"]]["truth"])
                elif e["class"] == "photo":
                    im = Image.open(path)
                    self.assertEqual(im.format, "JPEG")
                    self.assertGreaterEqual(im.width, 1000)
                    self.assertEqual(e["scope"], "triage")

    def test_review_findings_stay_fixed(self):
        """The independent Phase A review's findings (AM2): no exponent quantities, the
        right ETDA type code, duplicates judged against a stated history, e-Tax names
        from the XML."""
        for fid, e in self.exp.items():
            for l in e["truth"]["line_items"]:
                self.assertRegex(l["quantity"], r"^\d+(\.\d+)?$", fid)
            self.assertEqual(e["history"], ["d01_clean_full_tax_invoice"] if fid == "f03_duplicate_of_d01" else [], fid)
        import xml.etree.ElementTree as ET

        ram = "{urn:etda:uncefact:data:standard:TaxInvoice_ReusableAggregateBusinessInformationEntity:2}"
        root = ET.parse(os.path.join(E2, "docs", "e02_etax_xml_receipt.xml")).getroot()
        self.assertEqual(root.find(f".//{ram}TypeCode").text, "T03")   # ใบเสร็จรับเงิน/ใบกำกับภาษี
        self.assertEqual(self.exp["e01_etax_pdf_embedded_xml"]["truth"]["seller_name"]["value"],
                         "บริษัท เอเชีย คลาวด์ โซลูชั่นส์ จำกัด")
        self.assertEqual(self.exp["f03_duplicate_of_d01"]["checks"]["original_copy"], "fail")

    def test_the_duplicate_really_is_d01_again(self):
        a = self.exp["d01_clean_full_tax_invoice"]["truth"]
        b = self.exp["f03_duplicate_of_d01"]["truth"]
        for k in ("seller_tax_id", "document_number", "document_date", "total"):
            self.assertEqual(a[k], b[k])
        f = {x["id"]: x for x in self.index}
        self.assertNotEqual(list(f["d01_clean_full_tax_invoice"]["sha256"].values()),
                            list(f["f03_duplicate_of_d01"]["sha256"].values()))


class TemplatePack(unittest.TestCase):
    def test_templates_carry_their_markers(self):
        exp = _load(os.path.join(FIX, "templates", "expected.json"))
        base = os.path.join(FIX, "templates")
        with zipfile.ZipFile(os.path.join(base, "docx_placeholders.docx")) as z:
            xml = z.read("word/document.xml").decode() + z.read("word/header1.xml").decode() + z.read("word/footer1.xml").decode()
        for m in exp["templates"]["docx_placeholders.docx"]["markers"]:
            if m == "invoice_count":
                self.assertIn("{{invoice_", xml)          # split across two runs on purpose
                self.assertNotIn("{{invoice_count}}", xml)
            else:
                self.assertIn("{{" + m + "}}", xml)
        with zipfile.ZipFile(os.path.join(base, "docx_content_controls.docx")) as z:
            xml = z.read("word/document.xml").decode()
        self.assertEqual(xml.count("<w:sdt>"), 5)
        for kind in ("<w:text/>", "<w:richText/>", "<w:date>", "<w:dropDownList>"):
            self.assertIn(kind, xml)
        from openpyxl import load_workbook

        wb = load_workbook(os.path.join(base, "xlsx_named_ranges.xlsx"))
        self.assertTrue({"CompanyName", "Subtotal", "VatAmount", "GrandTotal", "LinesTable", "VatRate"} <= set(wb.defined_names))
        self.assertEqual(wb["สรุป"]["B7"].value, "=B5+B6")
        self.assertIn("A1:C1", [str(r) for r in wb["สรุป"].merged_cells.ranges])
        self.assertTrue(wb["ข้อมูลอ้างอิง"].protection.sheet)
        self.assertEqual(len(wb["สรุป"]._charts), 1)
        v = exp["values"]
        total = sum(D(l["amount"].replace(",", "")) for l in v["lines"])
        self.assertEqual(f"{total:,.2f}", v["total_amount"])


class ExpensePack(unittest.TestCase):
    def test_claims_receipts_and_expectations_line_up(self):
        base = os.path.join(FIX, "expense")
        exp = _load(os.path.join(base, "expected.json"))
        claims = {c["id"]: c for c in exp["claims"]}
        self.assertEqual(set(claims), set(exp["expected"]))
        for cid, c in claims.items():
            with self.subTest(cid):
                if c["receipt"]:
                    rid = os.path.basename(c["receipt"])[:-4]
                    self.assertEqual(D(exp["receipt_totals"][rid]), D(c["amount"]))
                    text = _norm(_pdf_text(os.path.join(base, c["receipt"])))
                    self.assertIn(f"{D(c['amount']):,.2f}", text)
        # every VAT-registered receipt seller has its own valid tax ID
        ids = {}
        for rid in exp["receipt_totals"]:
            t = re.sub(r"\D", "", _pdf_text(os.path.join(base, "receipts", rid + ".pdf")).split("TAX ID")[1][:30])                 if "TAX ID" in _pdf_text(os.path.join(base, "receipts", rid + ".pdf")) else ""
            if t:
                t = t[:13]
                self.assertTrue(_tax_ok(t), rid)
                self.assertNotIn(t, ids, f"{rid} shares a tax ID with {ids.get(t)}")
                ids[t] = rid
        self.assertGreaterEqual(len(ids), 4)
        outcomes = {v["outcome"] for v in exp["expected"].values()}
        self.assertEqual(outcomes, {"pass", "exception", "question"})
        for cid, v in exp["expected"].items():   # a rule produces a fact, a person decides: never "reject"
            self.assertIn(v["outcome"], ("pass", "exception", "question"), cid)
            self.assertFalse([c for c in v.get("codes", []) if "reject" in c], cid)
        # the monthly meals limit is crossed by C16 exactly
        e1 = sorted((c for c in claims.values() if c["employee"] == "E001" and c["category"] == "meals"), key=lambda c: c["date"])
        running, crossed = D(0), None
        for c in e1:
            running += D(c["amount"])
            if crossed is None and running > D("5000"):
                crossed = c["id"]
        self.assertEqual(crossed, "C16")


if __name__ == "__main__":
    unittest.main()
