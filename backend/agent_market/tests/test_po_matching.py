"""Agent 2 — invoice ↔ purchase order (AM6 section A).

  * the eval set, every case through the real steps (tests/pokit.py), its expected values from
    the scenario and plain arithmetic in tools/make_po_fixtures.py;
  * MATCH DEPTH: every line says whether it was tested three-way or two-way, the summary and the
    face of the working paper say it, and a two-way result never reads as a three-way one;
  * identical data, opposite meaning: the one setting that says whether a missing goods receipt
    is an exception or the normal case;
  * cumulatively: an over-billed line that no invoice shows by itself;
  * what nobody ordered is its own category and never part of a line's price variance;
  * three outcomes and none of them an approval;
  * tolerances are settings: the manifest declares them with the bounds the package checks;
  * a document is data: an address or a link printed on an invoice goes nowhere;
  * the same input twice gives the same computed.json, byte for byte, in another process with
    another hash seed;
  * no model decides a link, a number or a status.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from decimal import Decimal as D

from agent_market.engine import cells as C
from agent_market.engine.findings import FORBIDDEN_WORDS, display_len
from agent_market.packages import po_matching as pkg
from agent_market.packages.po_matching.checks import inputs as I
from agent_market.packages.po_matching.checks import links as L
from agent_market.packages.po_matching.checks import parties as P
from agent_market.packages.po_matching.checks import settings as S
from agent_market.packages.po_matching.checks import units as U
from agent_market.packages.po_matching.scripts import match as M
from agent_market.packages.po_matching.scripts.workbook import TWO_WAY_WARNING
from agent_market.packages.po_matching.scripts.workbook import W as WB_W   # AM18: the warning is a key, said when read
from agent_market.tests import pokit as K
from agent_market.tests import samplekit

FIX = K.FIX
_RESULTS: dict = {}
# what the card lists, and the case of the eval set that holds each
CARD_CASES = {
    "partial deliveries": "partial_delivery_second_invoice_before_second_receipt",
    "service PO, two-way": "service_po_is_two_way_and_says_so",
    "unplanned costs": "freight_nobody_ordered",
    "blanket PO": "blanket_po_drawn_down_over_months",
    "short-close": "invoice_after_a_short_close",
    "credit note": "credit_note_against_a_matched_invoice",
    "debit note": "debit_note_raises_the_price_past_tolerance",
    "GR/IR": "over_billed_only_cumulatively",
    "over-billed only cumulatively": "over_billed_only_cumulatively",
}
CARD_REASONS = ("price_above_po", "qty_over_received", "no_goods_receipt", "item_not_on_po", "po_closed", "duplicate_invoice",
                "supplier_mismatch", "currency_mismatch", "vat_mismatch", "tax_invoice_incomplete")


def case_of(cid: str) -> dict:
    return next(c for c in K.load_cases()["cases"] if c["id"] == cid)


def result(cid: str) -> tuple:
    if cid not in _RESULTS:
        case = case_of(cid)
        _RESULTS[cid] = (case, K.run_case(case))
    return _RESULTS[cid]


class EvalSet(unittest.TestCase):
    def test_every_case(self):
        cases = K.load_cases()["cases"]
        self.assertGreaterEqual(len(cases), 60)
        for case in cases:
            c, res = result(case["id"])
            self.assertEqual(K.check(c, res), [], case["id"])

    def test_the_set_holds_every_case_the_card_lists(self):
        ids = {c["id"] for c in K.load_cases()["cases"]}
        for what, cid in CARD_CASES.items():
            self.assertIn(cid, ids, what)

    def test_the_set_reaches_every_reason_code_and_every_tier(self):
        results = [result(c["id"])[1] for c in K.load_cases()["cases"]]
        b = K.breakdown(results)
        for code in CARD_REASONS:
            self.assertIn(code, b["reasons"], code)
        self.assertEqual(set(b["reasons"]), set(M.REASONS), "a reason code no case of the set produces")
        self.assertEqual(set(b["status"]), {"pass", "blocked", "person"})
        self.assertEqual(set(b["depth"]), {"three_way", "two_way", "none"})
        for tier in ("exact", "amount_date", "fuzzy"):
            self.assertGreater(b["tiers"][tier]["groups"], 0, tier)
        # below exact, nothing by rule — in the whole set
        self.assertEqual(b["tiers"]["amount_date"]["by_rule"] + b["tiers"]["fuzzy"]["by_rule"], 0)
        made = {k for k, p in b["passes"].items() if p["by_rule"] + p["by_person"] > 0}
        self.assertEqual(set(L.LABEL) - made, set(), "a pass no case of the set makes a link with")


class MatchDepth(unittest.TestCase):
    def test_identical_data_opposite_meaning(self):
        a, no = result("no_receipts_company_that_issues_none")
        b, yes = result("no_receipts_company_that_issues_them")
        self.assertEqual(a["files"], b["files"])
        self.assertEqual([f["code"] for f in no["asked"] + yes["asked"]], [])      # neither is asked a single question
        self.assertEqual([i["status"] for i in no["computed"]["invoices"]], ["pass"])
        self.assertEqual([(i["status"], i["reasons"]) for i in yes["computed"]["invoices"]], [("blocked", ["no_goods_receipt"])])
        for res in (no, yes):
            self.assertEqual({x["depth"] for x in res["computed"]["lines"]}, {"two_way"})

    def test_two_way_is_said_on_the_line_in_the_summary_and_on_the_face(self):
        _, res = result("service_po_is_two_way_and_says_so")
        c = res["computed"]
        line = c["lines"][0]
        self.assertEqual((line["depth"], line["depth_label"]), ("two_way", "ตรวจ 2 ทาง (PO · ใบแจ้งหนี้)"))
        self.assertEqual(line["depth_why"], "เป็นบริการ ไม่มีใบรับของ")
        self.assertEqual(line["receipts"], [])
        self.assertEqual(c["invoices"][0]["depth_text"], "2 ทาง 1 บรรทัด")
        self.assertTrue(c["invoices"][0]["two_way_only"])
        self.assertEqual([(d["depth"], d["lines"]) for d in c["summary"]["by_depth"]], [("three_way", 0), ("two_way", 1), ("none", 0)])
        self.assertIn("ตรวจ 2 ทาง 1 บรรทัด", c["fallback_narrative"]["movements"][0])
        sheets = res["sheets"]
        face = {r[0]: r[1] for r in sheets["สรุป"] if r and isinstance(r[0], str)}
        self.assertEqual(face["วิธีตรวจของรอบนี้"], "ตรวจ 3 ทาง 0 บรรทัด · ตรวจ 2 ทาง 1 บรรทัด")
        self.assertIn([WB_W(TWO_WAY_WARNING)], [[r[0]] for r in sheets["สรุป"] if r and r[0] == WB_W(TWO_WAY_WARNING)])
        # AM9: printed, the line's identity and amounts come first; the tables are read back by header name
        row = res["lines"][0]
        self.assertEqual((row[8], row[9], row[15]), ("ตรวจ 2 ทาง (PO · ใบแจ้งหนี้)", "เป็นบริการ ไม่มีใบรับของ", None))
        self.assertEqual(res["invoices"][0][13], "2 ทาง 1 บรรทัด")
        self.assertEqual(sheets["ผลตรวจรายบรรทัด"][3][:4], ["ลำดับ", "ใบแจ้งหนี้", "บรรทัด", "รายการ"])

    def test_a_round_that_is_all_three_way_has_no_warning(self):
        _, res = result("clean_three_way")
        self.assertNotIn(WB_W(TWO_WAY_WARNING), [r[0] for r in res["sheets"]["สรุป"] if r])
        for x in res["computed"]["lines"]:
            self.assertEqual(x["depth"], "three_way")
            self.assertTrue(all(g["file"] and g["sheet"] and g["row"] for g in x["receipts"]))

    def test_the_quality_gate_refuses_a_file_that_hides_the_depth(self):
        from openpyxl import load_workbook

        from agent_market.packages.po_matching.scripts.workbook import check

        import tempfile

        case = case_of("service_po_is_two_way_and_says_so")
        keep = tempfile.mkdtemp(prefix="am_po_")
        res = K.run_case(case, keep=keep)
        path = os.path.join(keep, K.BOOK)
        wb = load_workbook(path)
        ws = wb["สรุป"]
        for row in ws.iter_rows(min_row=1, max_col=2):
            if row[0].value == "วิธีตรวจของรอบนี้":
                row[1].value = "ตรวจแล้ว"
        wb.save(path)
        failed = [k["name"] for k in check(path, res["computed"]) if not k["ok"]]
        self.assertIn("xlsx.depth_on_face", failed)

    def test_a_receipt_confirmed_in_the_system_makes_the_line_three_way_and_names_who(self):
        _, res = result("receipt_confirmed_in_the_system")
        by = {x["n"]: x for x in res["computed"]["lines"]}
        self.assertEqual([by[n]["depth"] for n in (1, 2, 3)], ["three_way", "two_way", "two_way"])
        g = by[1]["receipts"][0]
        self.assertTrue(g["in_system"])
        self.assertEqual(by[1]["depth_why"], "มีการยืนยันรับของในระบบ")
        # offered, never required: the two lines that are still two-way
        self.assertEqual([o["line_no"] for o in res["computed"]["receipt_offers"]], ["20", "30"])
        entry = next(e for e in res["computed"]["index_entries"] if e["detail"]["kind"] == "receipt")
        self.assertEqual((entry["detail"]["akey"], entry["detail"]["quantity"]), ("PO6909001#l:10", "100"))

    def test_a_company_without_receipts_is_asked_nothing_about_them(self):
        _, res = result("no_receipts_company_that_issues_none")
        self.assertEqual(res["asked"], [])
        self.assertNotIn("no_goods_receipt", json.dumps(res["computed"]["run_exceptions"]))


class Cumulative(unittest.TestCase):
    def test_an_over_billed_line_that_no_invoice_shows_alone(self):
        case, res = result("over_billed_only_cumulatively")
        c = res["computed"]
        first, second = c["lines"]
        self.assertEqual((first["status"], second["status"]), ("pass", "blocked"))
        self.assertEqual(second["reasons"], ["qty_over_received"])
        self.assertEqual(second["cumulative"]["billed_before"], "60")
        self.assertEqual(second["cumulative"]["billed_after"], "120")
        self.assertEqual(second["cumulative"]["earlier"][0]["invoice"], "IV6909-0150")
        chk = next(k for k in second["checks"] if k["code"] == "qty_received")
        self.assertFalse(chk["ok"])
        self.assertEqual((chk["expected"], chk["actual"]), ("100", "120"))
        # each of the two, alone, is within what was received
        for name in ("inv_over_first.xlsx", "inv_over_second.xlsx"):
            alone = K.run_files([(os.path.join(FIX, n), n) for n in ("po_one.xlsx", "gr_one.xlsx", name)],
                                defaults={**K.load_cases()["defaults"], **case["defaults"]}, history=[])
            self.assertEqual([i["status"] for i in alone["computed"]["invoices"]], ["pass"], name)
        po = next(p for p in c["po_lines"] if p["line_no"] == "10")
        self.assertTrue(po["over_billed"])
        self.assertEqual((po["billed"], po["received"], po["billed_not_received"]), ("120", "100", "20"))

    def test_an_invoice_of_an_earlier_round_counts(self):
        _, res = result("over_billed_by_an_invoice_of_an_earlier_round")
        line = res["computed"]["lines"][0]
        self.assertEqual(line["cumulative"]["earlier"][0]["from"], "history")
        self.assertEqual(line["reasons"], ["qty_over_received"])

    def test_an_invoice_that_repeats_another_is_counted_once(self):
        _, res = result("the_same_invoice_in_a_later_round")
        c = res["computed"]
        self.assertEqual(c["invoices"][0]["duplicate_of"]["confirmed"], "rule")
        self.assertTrue(all(not x["counted"] for x in c["lines"]))
        self.assertEqual(c["index_entries"], [])
        self.assertEqual([p["billed"] for p in c["po_lines"]], ["100", "10", "60"])

    def test_what_a_round_records_is_what_the_next_starts_from(self):
        _, res = result("clean_three_way")
        entries = res["computed"]["index_entries"]
        self.assertEqual([e["document_number"] for e in entries], ["IV6909-0101", "BK6909-220"])
        lines = entries[0]["detail"]["lines"]
        self.assertEqual([(x["akey"], x["quantity"], x["amount"]) for x in lines],
                         [("PO6909001#l:10", "100", "12500.00"), ("PO6909001#l:20", "10", "18900.00"), ("PO6909001#l:30", "60", "3900.00")])
        self.assertEqual(entries[0]["detail"]["outcome"], "pass")


class NotOrdered(unittest.TestCase):
    def test_freight_is_its_own_category_and_never_a_price_variance(self):
        _, res = result("freight_nobody_ordered")
        c = res["computed"]
        pens, freight = c["lines"]
        self.assertEqual(pens["price_variance"], {"per_unit": "0", "pct": "0.00", "extended": "0.00"})
        self.assertEqual(freight["unplanned"], {"kind": "freight", "label": "ค่าขนส่ง", "amount": "450.00"})
        self.assertIsNone(freight["po_line"])
        self.assertEqual(c["invoices"][0]["reasons"], ["unplanned_cost"])
        self.assertEqual([r for r in res["sheets"]["ค่าใช้จ่ายที่ไม่ได้สั่งซื้อ"][4:] if r and r[0]][0][2:5],
                         ["ค่าขนส่ง", 450.0, "เกินวงเงินที่ตั้งไว้"])

    def test_the_words_are_the_installs(self):
        self.assertEqual(U.unplanned_kind("ค่าขนส่งสินค้า")[0], "freight")
        self.assertIsNone(U.unplanned_kind("กระดาษถ่ายเอกสาร A4"))
        s = S.settings_from({"ap_unplanned_words": "ค่าติดตั้ง, ค่าเดินทาง"})
        self.assertEqual(U.unplanned_kind("ค่าติดตั้งเครื่อง", s.unplanned_words)[0], "other")
        self.assertIsNone(U.unplanned_kind("ค่าขนส่ง", s.unplanned_words))


class Outcomes(unittest.TestCase):
    def test_three_and_none_of_them_is_an_approval(self):
        self.assertEqual(set(M.STATUS_TH), {"pass", "blocked", "person"})
        self.assertEqual(M.STATUS_TH["pass"], "ผ่าน · พร้อมเสนออนุมัติ")
        for code, (label, outcome) in M.REASONS.items():
            self.assertIn(outcome, ("blocked", "person"), code)
            self.assertTrue(label and M.CHOICES.get(code), code)
        _, res = result("clean_three_way")
        blob = json.dumps(res["computed"], ensure_ascii=False) + json.dumps(res["sheets"], ensure_ascii=False, default=str)
        for word in ("อนุมัติแล้ว", "จ่ายแล้ว", "approved", "paid"):
            self.assertNotIn(word, blob)
        self.assertIn("การอนุมัติและการจ่ายเป็นของคน", blob)

    def test_every_blocked_invoice_is_in_the_queue_with_choices(self):
        for cid in ("price_above_the_percentage", "currency_is_not_the_pos", "an_item_that_is_on_no_po"):
            _, res = result(cid)
            c = res["computed"]
            codes = {e["reason_code"] for e in c["run_exceptions"]}
            self.assertEqual(codes, {r for i in c["invoices"] for r in i["reasons"]}, cid)
            self.assertTrue(all(len(e["choices"]) >= 2 for e in c["run_exceptions"]), cid)


class Tolerances(unittest.TestCase):
    def test_the_manifest_declares_what_the_package_checks(self):
        m = samplekit.manifest("po_matching")
        declared = {s["key"]: s for s in m["install"]["settings"]}
        for key, (_attr, kind, lo, hi, default, label) in S.SETTINGS.items():
            d = declared[S.PREFIX + key]
            self.assertEqual((d["kind"], D(str(d["min"])), D(str(d["max"])), d["default"], d["label"]),
                             (kind, D(lo), D(hi), default, label), key)
            self.assertEqual(m["install"]["defaults"][S.PREFIX + key], default, key)
        self.assertEqual(declared["ap_gr_practice"]["kind"], "choice")
        self.assertNotIn("ap_gr_practice", m["install"]["defaults"])      # asked once, with the files' own answer
        self.assertEqual(S.settings_from(m["install"]["defaults"]).tolerances, S.Tolerances())

    def test_a_value_that_cannot_be_used_takes_its_default_and_is_named(self):
        s = S.settings_from({"ap_price_tolerance_pct": "มาก", "ap_small_difference": "500", "ap_vat_rate": "10"})
        self.assertEqual(sorted(s.unusable), ["price_tolerance_pct", "small_difference"])
        self.assertEqual((s.tolerances.price_pct, s.tolerances.small, s.tolerances.vat_rate), (D("2.00"), D("1.00"), D("10.00")))

    def test_both_limits_hold(self):
        _, capped = result("price_inside_the_percentage_past_the_cap")
        _, default = result("the_same_line_at_the_default_cap")
        self.assertEqual(capped["computed"]["lines"][0]["tolerances"]["price_tolerance_abs"], "300.00")
        self.assertEqual(default["computed"]["lines"][0]["tolerances"]["price_tolerance_abs"], "500.00")
        self.assertEqual((capped["computed"]["lines"][0]["status"], default["computed"]["lines"][0]["status"]), ("blocked", "pass"))

    def test_the_tax_rate_is_a_setting(self):
        case = case_of("clean_three_way")
        res = K.run_files([(os.path.join(FIX, n), n) for n in case["files"]], defaults={**case["defaults"], "ap_vat_rate": "10"},
                          history=[])
        self.assertEqual({r for i in res["computed"]["invoices"] for r in i["reasons"]}, {"vat_mismatch"})


class Reading(unittest.TestCase):
    def test_what_a_sheet_is_comes_from_the_sheet(self):
        def kind(*heads, title="", sheet="Sheet1"):
            return I._kind([C.h(x) for x in heads], title, sheet)[0]
        self.assertEqual(kind("เลขที่ใบสั่งซื้อ", "วันที่", "ผู้ขาย", "รายการ", "จำนวน", "ราคาต่อหน่วย"), "po")
        self.assertEqual(kind("เลขที่ใบรับของ", "วันที่รับของ", "เลขที่ใบสั่งซื้อ", "รหัสสินค้า", "จำนวนรับ"), "gr")
        self.assertEqual(kind("เลขที่ใบกำกับภาษี", "วันที่", "PO No.", "รายการ", "จำนวนเงิน"), "invoice")
        self.assertEqual(kind("PO No.", "Item Code", "Description", "Order Qty", "Unit Price"), "po")
        self.assertEqual(kind("เลขที่", "วันที่", "เลขที่ใบสั่งซื้อ", "รายการ", "จำนวน", title="รายงานการรับสินค้า"), "gr")
        self.assertEqual(kind("เลขที่", "วันที่", "เลขที่ใบสั่งซื้อ", "รายการ", "จำนวน", sheet="INV"), "invoice")
        self.assertIsNone(kind("เลขที่", "วันที่", "เลขที่ใบสั่งซื้อ", "รายการ", "จำนวน", "จำนวนเงิน"))
        self.assertIsNone(kind("เลขที่ใบรับของ", "เลขที่ใบกำกับภาษี", "เลขที่ใบสั่งซื้อ", "จำนวน"))

    def test_a_po_reference_in_a_documents_text(self):
        def refs(text):
            return I._text_refs(I.PO_LABEL, text)
        self.assertEqual(refs("อ้างอิงใบสั่งซื้อเลขที่ PO6909-001"), ["PO6909-001"])
        self.assertEqual(refs("อ้างอิงใบสังซือเลขที PO6909-001"), ["PO6909-001"])          # a text layer that lost its marks
        self.assertEqual(refs("PO No. 4500012345  Date 12/09/2569"), ["4500012345"])
        self.assertEqual(refs("P.O. No: PO-2569/0012"), ["PO-2569/0012"])
        self.assertEqual(refs("Purchase Order Number 778812"), ["778812"])
        self.assertEqual(refs("PO6909-001"), [])                                        # a number, no label
        self.assertEqual(refs("P.O. Box 10110 Bangkok"), [])
        self.assertEqual(refs("Reports2024 are attached"), [])
        self.assertEqual(I._text_refs(I.INV_LABEL, "อ้างถึงใบกำกับภาษีเลขที่ IV6909-0101"), ["IV6909-0101"])

    def test_a_tax_id_whose_zero_a_spreadsheet_dropped(self):
        tid = "0105557000118"
        self.assertTrue(C.tax_id_ok(tid))
        self.assertEqual(C.tax_id(105557000118), tid)
        self.assertEqual(C.tax_id(105557000118.0), tid)
        self.assertEqual(C.tax_id("0-1055-57000-11-8"), tid)
        self.assertEqual(C.tax_id(105557000119), "")          # twelve digits that do not check: not a tax ID
        self.assertEqual(C.tax_id("12345"), "")

    def test_quantities_and_prices_keep_their_decimals(self):
        self.assertEqual((C.number("0.0125"), C.number(12), C.number("1,250.5"), C.money("1,250.505")), ("0.0125", "12.00", "1250.50", "1250.51"))
        self.assertIsNone(C.number("สิบสอง"))

    def test_the_supplier_is_its_tax_id(self):
        a = P.Party("บริษัท เอเชีย ออฟฟิศ ซัพพลาย จำกัด", "0105557000118")
        self.assertEqual(P.compare(a, P.Party("ASIA OFFICE SUPPLY CO., LTD.", "0105557000118")), ("same", "tax_id"))
        self.assertEqual(P.compare(a, P.Party("บริษัท เอเชีย ออฟฟิศ ซัพพลาย จำกัด", "0103558000226")), ("different", "tax_id_differs"))
        self.assertEqual(P.compare(a, P.Party("บจก.เอเชียออฟฟิศซัพพลาย")), ("same", "name"))
        self.assertEqual(P.compare(a, P.Party("หจก. เอเชีย ออฟฟิศ ซัพพลาย")), ("unsure", "legal_form"))
        self.assertEqual(P.compare(a, P.Party("บริษัท กรุงเทพบรรจุภัณฑ์ จำกัด")), ("different", "name_differs"))

    def test_units(self):
        conv, ok = S.conversions_from("1 กล่อง = 12 ชิ้น; A-100: 1 ลัง = 24 ขวด")
        self.assertTrue(ok)
        self.assertEqual(U.factor("pcs", "ชิ้น"), (D("1"), "same"))
        self.assertEqual(U.factor("โหล", "PCS."), (D("12"), "universal"))
        self.assertEqual(U.factor("box", "ชิ้น", "", conv), (D("12"), "settings"))
        self.assertEqual(U.factor("ขวด", "ลัง", "A-100", conv)[1], "settings")
        self.assertEqual(U.factor("ขวด", "ลัง", "B-200", conv), (None, "unknown"))
        self.assertEqual(U.factor("ถุง", "ชิ้น"), (None, "unknown"))
        self.assertEqual(S.conversions_from("กล่องละ 12")[1], False)
        self.assertTrue(U.is_service_unit("เดือน") and U.is_service_unit("Job") and not U.is_service_unit("กล่อง"))


class Cards(unittest.TestCase):
    def test_every_card_of_the_set_is_one_a_person_can_answer(self):
        seen = set()
        for case in K.load_cases()["cases"]:
            _, res = result(case["id"])
            for f in res["asked"]:
                seen.add(f["code"])
                self.assertLessEqual(display_len(f["header"]), 12, f["code"])
                self.assertIn(f["default"], [o["value"] for o in f["options"]] + ["wait"], f["code"])
                low = (f["question"] + " ".join(o["label"] for o in f["options"])).lower()
                self.assertFalse([w for w in FORBIDDEN_WORDS if w in low], f["code"])
                words = re.sub(r"[^\s“”]+\.(?:xlsx|xlsm|csv|pdf|xml|jpe?g|png)", " ", f["question"])   # a file's name is the person's
                self.assertIsNone(re.search(r"[a-z]+_[a-z_]+", words), f["question"])
        self.assertEqual(seen, {"ap_gr_practice", "e3_candidate", "pomatch_kind", "pomatch_supplier", "pomatch_unit", "missing_file"})

    def test_a_link_is_never_made_by_a_skipped_card(self):
        _, res = result("a_proposed_link_nobody_confirms")
        card = res["asked"][0]
        self.assertEqual((card["default"], card.get("recommended")), ("undecided", None))
        self.assertEqual(res["computed"]["lines"][0]["po_line"], None)
        self.assertEqual(res["computed"]["totals"]["by_person"], 0)

    def test_a_link_a_person_confirmed_names_that_person(self):
        _, res = result("a_link_is_proposed_and_confirmed")
        link = res["computed"]["lines"][0]["link"]
        self.assertEqual((link["confirmed"], link["confirmed_by"], link["tier"], link["pass"]),
                         ("person", "สมหญิง ผู้ตรวจ", "amount_date", "po_price"))
        # AM10: who confirmed is on the line sheet's companion (เกณฑ์ที่ใช้); the kit reads the table across its sheets
        from agent_market.packages.po_matching.scripts.workbook import H_LINES

        self.assertEqual(res["lines"][0][H_LINES.index("ผู้ยืนยัน")], "คนยืนยัน: สมหญิง ผู้ตรวจ")

    def test_the_question_about_receipts_is_asked_once_and_can_be_remembered(self):
        _, res = result("practice_is_asked_once_with_a_guess")
        cards = [f for f in res["asked"] if f["code"] == "ap_gr_practice"]
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0]["rememberable"])
        self.assertEqual((cards[0]["recommended"], cards[0]["default"]), ("yes", "yes"))
        # with the setting, it is never asked
        _, res = result("clean_three_way")
        self.assertEqual(res["asked"], [])


class ADocumentIsData(unittest.TestCase):
    def test_an_address_printed_on_an_invoice_goes_nowhere(self):
        case, res = result("a_document_that_names_a_recipient")
        # the fixture does hold them: the reader saw the footer
        from agent_market.engine.e2.words import build_lines, pdf_text_words

        words, _ = pdf_text_words(os.path.join(FIX, case["files"][2]))
        text = " ".join(l.text for l in build_lines(words))
        self.assertIn("payments@collect-now.example", text)
        self.assertIn("https://collect-", text)
        blob = res["computed_bytes"].decode("utf-8") + json.dumps(res["summary"], ensure_ascii=False) + \
            json.dumps(res["sheets"], ensure_ascii=False, default=str) + json.dumps(res["preflight"], ensure_ascii=False)
        for word in ("collect-now", "payments@", "accounts@", "https://", "please send"):
            self.assertNotIn(word, blob, word)
        c = res["computed"]
        self.assertEqual([i["status"] for i in c["invoices"]], ["pass"])
        for key in ("recipients", "to", "cc", "email", "links"):
            self.assertNotIn(key, c)
            self.assertNotIn(key, res["summary"])


class SameInputTwice(unittest.TestCase):
    CASES = ("clean_three_way", "over_billed_only_cumulatively", "a_link_is_proposed_and_confirmed", "invoice_as_a_pdf")

    def test_byte_identical_computed_json(self):
        for cid in self.CASES:
            case = case_of(cid)
            a, b = K.run_case(case), K.run_case(case)
            self.assertEqual(a["computed_bytes"], b["computed_bytes"], cid)

    def test_upload_order_changes_nothing(self):
        case = case_of("clean_three_way")
        a = K.run_case(case)
        b = K.run_case({**case, "files": list(reversed(case["files"]))})
        self.assertEqual(a["computed_bytes"], b["computed_bytes"])

    def test_in_another_process_with_another_hash_seed(self):
        code = ("import hashlib, json, sys\n"
                "from agent_market.tests import pokit as K\n"
                "out = {}\n"
                "for cid in sys.argv[1:]:\n"
                "    case = next(c for c in K.load_cases()['cases'] if c['id'] == cid)\n"
                "    res = K.run_case(case)\n"
                "    c = res['computed']\n"
                "    out[cid] = [hashlib.sha256(res['computed_bytes']).hexdigest(),\n"
                "                hashlib.sha256(json.dumps(c['match'], ensure_ascii=False).encode()).hexdigest()]\n"
                "print(json.dumps(out))\n")
        got = []
        for seed in ("0", "4242"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": os.pathsep.join(sys.path)}
            p = subprocess.run([sys.executable, "-c", code, *self.CASES], capture_output=True, text=True, env=env, timeout=600)
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            got.append(json.loads(p.stdout.strip().splitlines()[-1]))
        self.assertEqual(got[0], got[1])


class SelfTest(unittest.TestCase):
    def test_the_sample_month_asks_nothing_and_passes(self):
        st = samplekit.self_test("po_matching")
        res = samplekit.render_sample("po_matching", defaults=samplekit.self_test_defaults("po_matching"))
        self.assertEqual(res["first_findings"], [])
        self.assertTrue(res["render"]["passed"])
        t = res["computed"]["totals"]
        self.assertEqual({k: t[k] for k in st["expect"]["totals"]}, st["expect"]["totals"])
        self.assertEqual(res["period"], st["expect"]["period"])
        for rel in st["files"]:
            self.assertTrue(os.path.isfile(os.path.join(samplekit.package_dir("po_matching"), rel)), rel)

    def test_the_manifest(self):
        m = samplekit.manifest("po_matching")
        self.assertEqual((m["id"], m["python_package"], m["narrative"]), ("sran.invoice-po-matching", "po_matching", "model"))   # AM22: a model writes its words
        self.assertEqual((m["scope"], m["jurisdiction"]), ("generic", "TH"))
        self.assertTrue(m["record_who_answered"])
        self.assertNotIn("completion_gate", m)           # finding a blocked invoice is the work, not a round that failed
        self.assertEqual(m["document_index"], {"retention_days": 1825})
        self.assertEqual(pkg.VERSION, m["version"])
        self.assertEqual(m["permissions"]["default"], "read_only")
        for denied in ("payment", "accounting_write", "email_send", "schedule"):
            self.assertIn(denied, m["permissions"]["denied"])
        self.assertEqual({i["role"]: i["required"] for i in m["inputs"]}, {"po": True, "invoice": True, "gr": False})


class NoModel(unittest.TestCase):
    def test_no_model_decides_a_link_a_number_or_a_status(self):
        m = samplekit.manifest("po_matching")
        # AM22: model.text writes the commentary from the computed table; no model decides a link, a number or a status
        # (AM23: the report, the deck and the PDF of the workbook a person asks for)
        self.assertEqual(m["capabilities"]["optional"], ["model.vision", "model.text", "deliverable.report.docx", "deliverable.deck.pptx",
                                                         "deliverable.pdf"])
        self.assertNotIn("model.text", m["capabilities"]["required"])
        base = os.path.dirname(os.path.abspath(pkg.__file__))
        for root, _, names in os.walk(base):
            for n in names:
                if not n.endswith(".py"):
                    continue
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    src = re.sub(r'"""[\s\S]*?"""', "", f.read())
                for banned in (r"\brandom\b", r"\.now\(", r"\.today\(", r"\brequests\b", r"\bhttpx\b", r"\burllib\b", r"\bopenai\b",
                               r"\bfloat\("):
                    if n == "workbook.py" and "float" in banned:
                        continue    # a cell of the file holds a number; every sum is made before, in Decimal
                    self.assertIsNone(re.search(banned, src), (n, banned))
                # a model's reading reaches the document reader as a candidate and nothing else
                if n in ("match.py", "links.py", "book.py", "units.py", "parties.py", "settings.py", "workbook.py"):
                    self.assertNotIn("proposals[", src, n)
        _, res = result("clean_three_way")
        self.assertEqual(res["computed"]["models"], [])


if __name__ == "__main__":
    unittest.main()
